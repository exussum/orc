import random
import time
from dataclasses import dataclass, replace
from datetime import date, timedelta
from functools import wraps
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from flask import Blueprint
from flask import current_app as app
from flask import redirect, render_template, request

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
    return (
        render_template(
            "button.html",
            other_configs=config.OTHER_CONFIGS,
            room_configs=config.ROOM_CONFIGS,
            theme_configs=config.THEME_CONFIGS,
            schedule_routines=config.SCHEDULE_ROUTINES,
        ),
        200,
        {"Cache-control": "max-age=604800"},
    )


@bp.route("/remote/<id>")
def remote(id):
    if id in ("TV Lights", "Partial TV Lights"):
        end = api.local_now() + timedelta(hours=3)
        app.config_manager.replace_config(config.THEME_CONFIGS[id], end)
    else:
        app.config_manager.resume(config.ALL_CONFIGS[id])
        app.version_manager.bump_version()
    return {}, 200


@bp.route("/console/<id>")
def console(id):
    if id == "Test":
        end = api.local_now() + timedelta(minutes=10)
        app.config_manager.replace_config(m.Config(config.Light, config.OFF), end)
        api.test(config.OTHER_CONFIGS[id])
        app.config_manager.resume(config.DEFAULT_CONFIG)
    elif id == "Restore Snapshot":
        app.config_manager.resume(config.DEFAULT_CONFIG)
    elif id == "Replay Day":
        now = api.local_now()
        jobs = sorted(api.get_schedule(app.config_manager), key=lambda x: x[0])
        configs = (config for (when, config) in jobs if when <= now)
        api.execute(api.squish_configs(*configs))
    elif id in config.OTHER_CONFIGS:
        api.execute(config.OTHER_CONFIGS[id])
    elif id in config.SCHEDULE_ROUTINES:
        api.execute(config.SCHEDULE_ROUTINES[id])
    elif id in config.THEME_CONFIGS:
        api.execute(api.squish_configs(m.Configs(*config.ROUTINE_RESET_LIGHT.items), config.THEME_CONFIGS[id]))
    return {}, 200


@bp.route("/room/<id>")
def room(id):
    state = request.args.get("state")
    if state == config.ON:
        api.execute(config.ROOM_CONFIGS[id])
    elif state == config.OFF:
        api.execute(m.Configs(*(replace(e, state=config.OFF) for e in config.ROOM_CONFIGS[id].items)))
    elif state == "follow":
        api.execute(api.squish_configs(config.ROOM_CONFIGS_OFF, config.ROOM_CONFIGS[id]))
    else:
        raise Exception("Unknown state")

    return {}, 200


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


@bp.route("/schedule/set_theme", methods=["POST"])
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
    return redirect("/schedule/")


@bp.route("/schedule/<id>/pause")
@VersionManager.versioned
def pause(id):
    job = app.scheduler.get_job(id)
    if job.next_run_time:
        job.pause()
    else:
        job.resume()


@bp.route("/schedule/<id>/run")
@VersionManager.versioned
def run(id):
    job = app.scheduler.get_job(id)
    job.func(True)
