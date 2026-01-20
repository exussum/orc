import itertools
import random
import time
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from functools import wraps
from typing import List
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from flask import redirect, request
from flask_admin import expose
from flask_admin.base import AdminIndexView

from orc import api, config
from orc import model as m


class VersionedView:
    version = str(random.random())
    snapshot = None

    @classmethod
    def bump_version(cls):
        cls.version = str(random.random())

    @staticmethod
    def versioned(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not request.headers.get("orc-version") == VersionedView.version:
                return {"version": VersionedView.version}, 412
            func(*args, **kwargs)
            VersionedView.version = str(random.random())
            return {"version": VersionedView.version}, 200

        return wrapper


class ButtonView(AdminIndexView, VersionedView):
    def __init__(self, name, url, config_manager):
        super(AdminIndexView, self).__init__(url=url, name=name)
        self.config_manager = config_manager

    @expose("/")
    def index(self):
        return (
            self.render(
                "button.html",
                version=self.version,
                other_configs=config.OTHER_CONFIGS,
                room_configs=config.ROOM_CONFIGS,
                theme_configs=config.THEME_CONFIGS,
            ),
            200,
        )

    @expose("/remote/<id>")
    def remote(self, id):
        if id in ("TV Lights", "Partial TV Lights"):
            end = datetime.now(tz=config.TZ) + timedelta(hours=4)
            self.config_manager.replace_config(config.THEME_CONFIGS[id], end)
        else:
            self.config_manager.resume(config.ALL_CONFIGS[id])

        self.bump_version()
        return {}, 200

    @expose("/console/<id>")
    def console(self, id):
        if id == "Test":
            end = datetime.now(tz=config.TZ) + timedelta(minutes=10)
            self.config_manager.replace_config(m.LightSimpleConfig(what=config.Light, state=config.OFF), end)
            api.test(config.OTHER_CONFIGS[id])
            self.config_manager.resume(config.DEFAULT_CONFIG)
        elif id in config.OTHER_CONFIGS:
            api.execute(config.OTHER_CONFIGS[id])
        elif id in config.THEME_CONFIGS:
            routine = api.squish_routines(m.AdHocConfig(items=(config.CONFIG_RESET_LIGHT.items)), config.THEME_CONFIGS[id])
            api.execute(routine)

        self.bump_version()
        return {}, 200

    @expose("/room/<id>")
    def room(self, id):
        state = request.args.get("state")
        if state == config.ON:
            api.execute(config.ROOM_CONFIGS[id])
        elif state == config.OFF:
            api.execute(m.AdHocConfig(items=(replace(e, state=config.OFF) for e in config.ROOM_CONFIGS[id].items)))
        elif state == "follow":
            items = itertools.chain.from_iterable(v.items for (k, v) in config.ROOM_CONFIGS.items() if id != k)
            api.execute(config.ROOM_CONFIGS[id])
            api.execute(m.AdHocConfig(items=(replace(e, state=config.OFF) for e in items)))
        else:
            raise Exception("Unknown state")

        self.bump_version()
        return {}, 200


class ScheduleView(AdminIndexView, VersionedView):
    def __init__(self, name, url, config_manager, scheduler):
        super(AdminIndexView, self).__init__(url=url, name=name)
        self.scheduler = scheduler
        self.config_manager = config_manager

    @expose("/")
    def index(self):
        jobs = sorted(api.non_cron_jobs(self.scheduler), key=lambda e: e.trigger.run_date)
        theme_override = self.config_manager.theme_override
        theme = (
            theme_override._replace(start=theme_override.start.isoformat(), end=theme_override.end.isoformat()) if theme_override else None
        )
        return (
            self.render("schedule.html", version=self.version, jobs=jobs, theme=theme),
            200,
            {"Cache-control": "no-store"},
        )

    @expose("/set_theme", methods=["POST"])
    def set_theme(self):
        if not request.form["theme"]:
            self.config_manager.theme_override = None
        else:
            self.config_manager.set_theme_override(
                request.form["theme"],
                date.fromisoformat(request.form["start"]),
                date.fromisoformat(request.form["end"]),
            )
        self.scheduler.remove_all_jobs()
        api.setup_scheduler(self.scheduler, self.config_manager)
        return redirect("/")

    @VersionedView.versioned
    @expose("/<id>/pause")
    def pause(self, id):
        job = self.scheduler.get_job(id)
        if job.next_run_time:
            job.pause()
        else:
            job.resume()

    @VersionedView.versioned
    @expose("/<id>/run")
    def run(self, id):
        job = self.scheduler.get_job(id)
        job.func(True)
