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

import orc
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
    eligible = {r.name for (_, r) in api.get_schedule(app.orc.config_manager) if not any(cfg.mandatory for cfg in r.items)}
    jobs = sorted(api.jobs_by_type(app.orc.scheduler, m.IotJob), key=lambda e: e.trigger.run_date)
    next_schedule = next((e for e in jobs if e.name in eligible and e.next_run_time), None)

    return (
        render_template(
            "button.html",
            highlight_configs=config.button_highlight_configs,
            super_routines=config.super_routines,
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


@bp.route("/schedule/")
def schedule():
    jobs = sorted(api.jobs_by_type(app.orc.scheduler, m.IotJob), key=lambda e: e.trigger.run_date)
    theme_override = app.orc.config_manager.theme_override

    theme = theme_override._replace(start=theme_override.start.isoformat(), end=theme_override.end.isoformat()) if theme_override else None

    return (
        render_template(
            "schedule.html",
            version=app.orc.version_manager.version,
            jobs=jobs,
            theme=theme,
            durations=config.durations,
        ),
        200,
        {"Cache-control": "max-age=604800"},
    )


@bp.route("/config/")
def cfg():
    with open(config.orc_config) as f:
        return render_template("config.html", html=HtmlRenderer().render(Document(f)))


@bp.route("/log/")
def log():
    return (
        render_template("log.html", version=app.orc.version_manager.version, entries=api.log_entries()),
        200,
        {"Cache-control": "no-store"},
    )


@bp.route("/api/version")
def version():
    return {"version": app.orc.version_manager.version}, 200


@bp.route("/api/remote/<id>")
def remote(id):
    api.log(api.local_now(), m.LogSource.REMOTE, id)
    if id in ("TV Lights", "Partial TV Lights"):
        end = api.local_now() + timedelta(hours=3)
        app.orc.config_manager.replace_config(config.ad_hoc_routines[id], end)
    else:
        app.orc.config_manager.resume(config.all_configs[id])
    app.orc.version_manager.bump_version()
    return {}, 200


@bp.route("/api/console/<id>")
def console(id):
    api.log(api.local_now(), m.LogSource.MANUAL, id)
    if id == "Reboot":
        os.kill(os.getppid(), signal.SIGTERM)
    elif id == "Light Test":
        end = api.local_now() + timedelta(minutes=10)
        app.orc.config_manager.replace_config(m.Config(orc.Light, config.OFF), end)
        api.test(config.super_routines[id])
        app.orc.config_manager.resume(config.default_config)
    elif id == "Sound Test":
        api.play_alert(app.orc.sound_path)
        api.play_text("audio test")
    elif id == "Back on Schedule":
        now = api.local_now()
        jobs = sorted(api.get_schedule(app.orc.config_manager), key=lambda x: x[0])
        configs = (config for (when, config) in jobs if when <= now)
        api.execute(m.squish_configs(*configs))
    elif id in config.super_routines:
        api.execute(config.super_routines[id])
    elif id in config.schedule_routines:
        api.execute(config.schedule_routines[id])
    elif id in config.ad_hoc_routines:
        api.execute(m.squish_configs(config.reset_config, config.ad_hoc_routines[id]))
    else:
        raise Exception("Unknown routine")
    return {}, 200


@bp.route("/api/room/<id>")
def room(id):
    state = request.args.get("state")
    api.log(api.local_now(), m.LogSource.MANUAL, f"Room: {id} {state}")
    if state == config.ON:
        api.execute(config.room_configs[id])
    elif state == config.OFF:
        api.execute(m.Configs(*(replace(e, state=config.OFF) for e in config.room_configs[id].items)))
    elif state == "follow":
        api.execute(m.squish_configs(config.room_configs_off, config.room_configs[id]))
    else:
        raise Exception("Unknown state")

    return {}, 200


@bp.route("/api/schedule/set_theme", methods=["POST"])
@VersionManager.versioned
def set_theme():
    if not request.form["theme"]:
        app.orc.config_manager.theme_override = None
    else:
        app.orc.config_manager.set_theme_override(
            request.form["theme"],
            date.fromisoformat(request.form["start"]),
            date.fromisoformat(request.form["end"]),
        )
    app.orc.scheduler.remove_all_jobs()
    api.setup_scheduler(app.orc)


@bp.route("/api/schedule/<id>/pause")
@VersionManager.versioned
def pause(id):
    job = app.orc.scheduler.get_job(id)
    if job.next_run_time:
        job.pause()
    else:
        job.resume()


@bp.route("/api/schedule/<id>/run")
@VersionManager.versioned
def run(id):
    job = app.orc.scheduler.get_job(id)
    api.log(api.local_now(), m.LogSource.MANUAL, f"Force run: {job.name}")
    job.func(*job.args, ctx=app.orc, force=True)
