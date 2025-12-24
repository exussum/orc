import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import wraps
from typing import List
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from flask import redirect, request
from flask_admin import expose
from flask_admin.base import AdminIndexView

from orc import api, config


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
        return self.render("button.html", version=self.version, buttons=(config.BUTTON_CONFIGS.keys())), 200

    @expose("/button/<id>")
    def press(self, id, remote=False):
        if remote:
            if id == "TV Lights":
                end = datetime.now(tz=config.TZ) + timedelta(hours=4)
                self.config_manager.replace_config(config.BUTTON_CONFIGS["TV Lights"], end)
            elif id == "Partial TV Lights":
                end = datetime.now(tz=config.TZ) + timedelta(hours=4)
                self.config_manager.replace_config(config.BUTTON_CONFIGS["Partial TV Lights"], end)
            elif id == "Front Rooms":
                self.config_manager.resume(config.BUTTON_CONFIGS["Front Rooms"])
        else:
            api.execute(config.BUTTON_CONFIGS[id])

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
            theme_override._replace(start=theme_override.start.isoformat(), end=theme_override.end.isoformat())
            if theme_override
            else None
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
