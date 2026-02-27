import random
from dataclasses import replace
from datetime import date, timedelta
from functools import wraps

from flask import Blueprint
from flask import current_app as app
from flask import render_template, request

from orc import api, config
from orc import model as m

bp = Blueprint("button", __name__)


class VersionManager:
    version = str(random.random())
    snapshot = None

    @classmethod
    def bump_version(cls):
        cls.version = str(random.random())

    @staticmethod
    def versioned(func):
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
    jobs = sorted(api.non_cron_jobs(app.scheduler), key=lambda e: e.trigger.run_date)
    next_schedule = next((e for e in jobs if e.name in eligible and e.next_run_time), None)

    return render_template(
        "button.html",
        other_configs=config.OTHER_CONFIGS,
        room_configs=config.ROOM_CONFIGS,
        theme_configs=config.THEME_CONFIGS,
        schedule_routines=config.SCHEDULE_ROUTINES,
        next_routine=next_schedule,
        version=app.version_manager.version,
    )


@bp.route("/schedule/")
def schedule():
    jobs = sorted(api.non_cron_jobs(app.scheduler), key=lambda e: e.trigger.run_date)
    theme_override = app.config_manager.theme_override

    theme = theme_override._replace(start=theme_override.start.isoformat(), end=theme_override.end.isoformat()) if theme_override else None

    return (
        render_template("schedule.html", version=app.version_manager.version, jobs=jobs, theme=theme),
        200,
        {"Cache-control": "no-store"},
    )


@bp.route("/api/remote/<id>")
def remote(id):
    if id in ("TV Lights", "Partial TV Lights"):
        end = api.local_now() + timedelta(hours=3)
        app.config_manager.replace_config(config.THEME_CONFIGS[id], end)
    else:
        app.config_manager.resume(config.ALL_CONFIGS[id])
        app.version_manager.bump_version()
    return {}, 200


@bp.route("/api/console/<id>")
def console(id):
    if id == "Test":
        end = api.local_now() + timedelta(minutes=10)
        app.config_manager.replace_config(m.Config(config.Light, config.OFF), end)
        api.test(config.OTHER_CONFIGS[id])
        app.config_manager.resume(config.DEFAULT_CONFIG)
    elif id == "Restore Snapshot":
        app.config_manager.resume(config.DEFAULT_CONFIG)
    elif id == "Back on Schedule":
        now = api.local_now()
        jobs = sorted(api.get_schedule(app.config_manager), key=lambda x: x[0])
        configs = (config for (when, config) in jobs if when <= now)
        api.execute(m.squish_configs(*configs))
    elif id in config.OTHER_CONFIGS:
        api.execute(config.OTHER_CONFIGS[id])
    elif id in config.SCHEDULE_ROUTINES:
        api.execute(config.SCHEDULE_ROUTINES[id])
    elif id in config.THEME_CONFIGS:
        api.execute(m.squish_configs(config.RESET_CONFIG, config.THEME_CONFIGS[id]))
    else:
        raise Exception("Unknown routine")
    return {}, 200


@bp.route("/api/room/<id>")
def room(id):
    state = request.args.get("state")
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
    api.setup_scheduler(app.scheduler, app.config_manager)


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
    job.func(True)
