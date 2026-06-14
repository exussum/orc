import random
from collections.abc import Callable
from dataclasses import replace
from datetime import date, timedelta
from functools import wraps

from flask import Blueprint
from flask import current_app as app
from flask import render_template, request
from mistletoe import Document, HtmlRenderer

from orc import api, config
from orc import model as m
from orc import plugins
from orc.locale import Log

bp = Blueprint("button", __name__)


@bp.after_request
def no_cache(response):
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


class VersionManager:
    version = str(random.random())

    @classmethod
    def bump_version(cls):
        cls.version = str(random.random())

    @staticmethod
    def versioned(func: Callable[..., None]) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not request.headers.get("orc-version") == VersionManager.version:
                return {"version": VersionManager.version}, 412
            func(*args, **kwargs)
            VersionManager.version = str(random.random())
            return {"version": VersionManager.version}, 200

        return wrapper


@bp.route("/config/")
def cfg():
    today = date.today()
    tomorrow = today + timedelta(days=1)
    with open(config.orc_config) as f:
        return render_template(
            "config.html",
            html=HtmlRenderer().render(Document(f)),
            ctx=app.orc,
            today_theme=api.calculate_theme(app.orc.config_manager, today),
            tomorrow_theme=api.calculate_theme(app.orc.config_manager, tomorrow),
            lights=api.capture_lights(),
            sounds=api.capture_sounds(),
        )


@bp.route("/api/console/<id>")
def console(id):
    api.log(api.local_now(), m.LogSource.MANUAL, id)

    if id in config.plugins:
        plugins.execute_plugin(app.orc.config_manager, id)
    elif id in config.schedule_routines:
        api.execute(config.schedule_routines[id])
    elif id in config.ad_hoc_routines:
        api.execute(m.squish_configs(config.reset_config, config.ad_hoc_routines[id]))
    else:
        raise Exception("Unknown routine")
    return {}, 200


@bp.route("/api/presence/<name>/checkin", methods=["POST"])
@VersionManager.versioned
def checkin_presence(name):
    api.mark_present(app.orc.config_manager, [name])
    api.log(api.local_now(), m.LogSource.MANUAL, Log.PRESENCE_CHECKED_IN.format(name=name))


@bp.route("/api/presence/<name>/expire", methods=["POST"])
@VersionManager.versioned
def expire_presence(name):
    api.expire_presence(app.orc.config_manager, name)
    api.log(api.local_now(), m.LogSource.MANUAL, Log.PRESENCE_EXPIRED.format(name=name))


@bp.route("/api/hubitat/callback", methods=["POST"])
def hubitat_callback():
    ctx = plugins.build_ctx(app.orc.config_manager, app.orc.scheduler)
    device_id = request.json["content"]["deviceId"]
    value = request.json["content"]["value"]
    plugins.trigger_sensor(ctx, device_id, value)
    return {}, 200


@bp.route("/")
def index():
    present_names = app.orc.config_manager.present_names
    next_schedule = api.next_iot_job(app.orc.scheduler, present_names)

    return (
        render_template(
            "button.html",
            highlight_configs=config.button_highlight_configs,
            plugins=config.plugins,
            room_configs=config.room_configs,
            ad_hoc_routines=config.ad_hoc_routines,
            schedule_routines=config.schedule_routines,
            next_routine=next_schedule,
            durations=config.durations,
            version=app.orc.version_manager.version,
        ),
        200,
        {"Cache-control": "max-age=604800"},
    )


@bp.route("/log/")
def log():
    return (
        render_template("log.html", version=app.orc.version_manager.version, entries=api.log_entries()),
        200,
        {"Cache-control": "no-store"},
    )


@bp.route("/api/schedule/<id>/pause")
@VersionManager.versioned
def pause(id):
    job = app.orc.scheduler.get_job(id)
    if job.next_run_time:
        job.pause()
    else:
        job.resume()


@bp.route("/presence/")
def presence():
    cm = app.orc.config_manager
    last_seen = cm.presence()
    present = cm.present_names
    rows = [
        {
            "name": name,
            "hostnames": sorted(hostnames),
            "last_seen": last_seen.get(name),
            "present": name in present,
        }
        for name, hostnames in config.people.items()
    ]
    return (
        render_template(
            "presence.html",
            version=app.orc.version_manager.version,
            rows=rows,
            strip_suffix=("." + config.root_domain) if config.root_domain else "",
        ),
        200,
        {"Cache-control": "no-store"},
    )


@bp.route("/api/remote/<id>")
def remote(id):
    api.log(api.local_now(), m.LogSource.REMOTE, id)
    if id in ("TV Lights", "Partial TV Lights"):
        api.replace_config_for(app.orc.config_manager, id, timedelta(hours=3))
    else:
        app.orc.config_manager.resume(config.all_configs[id])
    app.orc.version_manager.bump_version()
    return {}, 200


@bp.route("/api/room/<id>")
def room(id):
    state = request.args.get("state")
    api.log(api.local_now(), m.LogSource.MANUAL, Log.ROOM_SET.format(id=id, state=state))
    if state == config.ON:
        api.execute(config.room_configs[id])
    elif state == config.OFF:
        api.execute(m.Configs(*(replace(e, state=config.OFF) for e in config.room_configs[id].items)))
    elif state == config.FOLLOW:
        api.execute(m.squish_configs(config.room_configs_off, config.room_configs[id]))
    else:
        raise Exception("Unknown state")

    return {}, 200


@bp.route("/api/schedule/<id>/run")
@VersionManager.versioned
def run(id):
    job = app.orc.scheduler.get_job(id)
    api.log(api.local_now(), m.LogSource.MANUAL, Log.JOB_FORCED.format(job_name=job.name))
    job.func(*job.args, ctx=app.orc, force=True)


@bp.route("/api/presence/run", methods=["POST"])
@VersionManager.versioned
def run_presence_check():
    job = app.orc.scheduler.get_job("presence-cron")
    api.log(api.local_now(), m.LogSource.MANUAL, Log.JOB_FORCED.format(job_name=job.name))
    api.delete_all_presence(app.orc.config_manager)
    job.func(ctx=app.orc)


@bp.route("/schedule/")
def schedule():
    jobs = sorted(api.jobs_by_type(app.orc.scheduler, m.IotJob), key=lambda e: e.trigger.run_date)
    theme_override = app.orc.config_manager.theme_override

    theme = theme_override._replace(start=theme_override.start.isoformat(), end=theme_override.end.isoformat()) if theme_override else None

    present_names = app.orc.config_manager.present_names
    absent_by_job = {j.id: api.should_skip_for_presence(j.args[0].rule, False, present_names) for j in jobs}

    return (
        render_template(
            "schedule.html",
            version=app.orc.version_manager.version,
            jobs=jobs,
            theme=theme,
            durations=config.durations,
            absent_by_job=absent_by_job,
        ),
        200,
        {"Cache-control": "max-age=604800"},
    )


@bp.route("/api/schedule/set_theme", methods=["POST"])
@VersionManager.versioned
def set_theme():
    name = request.form["theme"]
    start = date.fromisoformat(request.form["start"]) if name else None
    end = date.fromisoformat(request.form["end"]) if name else None
    api.apply_theme_change(app.orc, name, start, end)


@bp.route("/api/version")
def version():
    return {"version": app.orc.version_manager.version}, 200


@bp.route("/api/durations")
def durations():
    return config.durations, 200
