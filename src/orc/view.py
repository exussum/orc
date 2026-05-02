import os
import random
import signal
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


@bp.route("/")
def index():
    eligible = {r.name for (_, r) in api.get_schedule(app.config_manager) if not any(cfg.mandatory for cfg in r.items)}
    jobs = sorted(api.jobs_by_type(app.scheduler, m.IotJob), key=lambda e: e.trigger.run_date)
    next_schedule = next((e for e in jobs if e.name in eligible and e.next_run_time), None)

    return (
        render_template(
            "button.html",
            highlight_configs=config.BUTTON_HIGHLIGHT_CONFIGS,
            super_routines=config.SUPER_ROUTINES,
            room_configs=config.ROOM_CONFIGS,
            ad_hoc_routines=config.AD_HOC_ROUTINES,
            schedule_routines=config.SCHEDULE_ROUTINES,
            next_routine=next_schedule,
            version=app.version_manager.version,
        ),
        200,
        {"Cache-control": "max-age=604800"},
    )


@bp.route("/schedule/")
def schedule():
    jobs = sorted(api.jobs_by_type(app.scheduler, m.IotJob), key=lambda e: e.trigger.run_date)
    theme_override = app.config_manager.theme_override

    theme = theme_override._replace(start=theme_override.start.isoformat(), end=theme_override.end.isoformat()) if theme_override else None

    return (
        render_template("schedule.html", version=app.version_manager.version, jobs=jobs, theme=theme),
        200,
        {"Cache-control": "max-age=604800"},
    )


@bp.route("/config/")
def cfg():
    with open(config.ORC_CONFIG) as f:
        return render_template("config.html", html=HtmlRenderer().render(Document(f)))


@bp.route("/log/")
def log():
    return (
        render_template("log.html", version=app.version_manager.version, entries=api.log_entries()),
        200,
        {"Cache-control": "no-store"},
    )


@bp.route("/api/version")
def version():
    return {"version": app.version_manager.version}, 200


@bp.route("/api/remote/<id>")
def remote(id):
    api.log(api.local_now(), m.LogSource.REMOTE, id)
    if id in ("TV Lights", "Partial TV Lights"):
        end = api.local_now() + timedelta(hours=3)
        app.config_manager.replace_config(config.AD_HOC_ROUTINES[id], end)
    else:
        app.config_manager.resume(config.ALL_CONFIGS[id])
    app.version_manager.bump_version()
    return {}, 200


@bp.route("/api/console/<id>")
def console(id):
    api.log(api.local_now(), m.LogSource.MANUAL, id)
    if id == "Reboot":
        os.kill(os.getpid(), signal.SIGTERM)
    elif id == "Light Test":
        end = api.local_now() + timedelta(minutes=10)
        app.config_manager.replace_config(m.Config(config.Light, config.OFF), end)
        api.test(config.SUPER_ROUTINES[id])
        app.config_manager.resume(config.DEFAULT_CONFIG)
    elif id == "Sound Test":
        api.play_alert(app.sound_path)
        api.play_text("audio test")
    elif id == "Back on Schedule":
        now = api.local_now()
        jobs = sorted(api.get_schedule(app.config_manager), key=lambda x: x[0])
        configs = (config for (when, config) in jobs if when <= now)
        api.execute(m.squish_configs(*configs))
    elif id in config.SUPER_ROUTINES:
        api.execute(config.SUPER_ROUTINES[id])
    elif id in config.SCHEDULE_ROUTINES:
        api.execute(config.SCHEDULE_ROUTINES[id])
    elif id in config.AD_HOC_ROUTINES:
        api.execute(m.squish_configs(config.RESET_CONFIG, config.AD_HOC_ROUTINES[id]))
    else:
        raise Exception("Unknown routine")
    return {}, 200


@bp.route("/api/room/<id>")
def room(id):
    state = request.args.get("state")
    api.log(api.local_now(), m.LogSource.MANUAL, f"Room: {id} {state}")
    if state == config.ON:
        api.execute(config.ROOM_CONFIGS[id])
    elif state == config.OFF:
        api.execute(m.Configs(*(replace(e, state=config.OFF) for e in config.ROOM_CONFIGS[id].items)))
    elif state == "follow":
        api.execute(m.squish_configs(config.ROOM_CONFIGS_OFF, config.ROOM_CONFIGS[id]))
    else:
        raise Exception("Unknown state")

    return {}, 200


@bp.route("/api/schedule/set_theme", methods=["POST"])
@VersionManager.versioned
def set_theme():
    if not request.form["theme"]:
        app.config_manager.theme_override = None
    else:
        app.config_manager.set_theme_override(
            request.form["theme"],
            date.fromisoformat(request.form["start"]),
            date.fromisoformat(request.form["end"]),
        )
    app.scheduler.remove_all_jobs()
    api.setup_iot_scheduler(app.scheduler, app.config_manager)


@bp.route("/api/schedule/<id>/pause")
@VersionManager.versioned
def pause(id):
    job = app.scheduler.get_job(id)
    if job.next_run_time:
        job.pause()
    else:
        job.resume()


@bp.route("/api/schedule/<id>/run")
@VersionManager.versioned
def run(id):
    job = app.scheduler.get_job(id)
    api.log(api.local_now(), m.LogSource.MANUAL, f"Force run: {job.name}")
    job.func(True)
