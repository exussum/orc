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


class OrcAdminView(AdminIndexView):
    version = str(random.random())
    snapshot = None

    def __init__(self, url, config_manager, scheduler):
        super(AdminIndexView, self).__init__(url=url)
        self.scheduler = scheduler
        self.config_manager = config_manager

    @classmethod
    def bump_version(cls):
        cls.version = str(random.random())

    @staticmethod
    def versioned(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not request.headers.get("orc-version") == OrcAdminView.version:
                return {"version": OrcAdminView.version}, 412
            func(*args, **kwargs)
            OrcAdminView.version = str(random.random())
            return {"version": OrcAdminView.version}, 200

        return wrapper

    @expose("/")
    def index(self):
        jobs = sorted(api.non_cron_jobs(self.scheduler), key=lambda e: e.trigger.run_date)
        theme_override = self.config_manager.theme_override
        theme = (
            theme_override._replace(start=theme_override.start.isoformat(), end=theme_override.end.isoformat())
            if theme_override
            else None
        )
        return self.render("orc.html", version=self.version, jobs=jobs, theme=theme), 200, {"Cache-control": "no-store"}

    @expose("/tv_mode_on")
    def tv_mode_on(self):
        end = datetime.now(tz=config.TZ) + timedelta(hours=4)
        self.config_manager.replace_config(config.CONFIG_TV_LIGHTS, end)
        self.bump_version()
        return {}, 200

    @expose("/tv_mode_off")
    def tv_mode_off(self):
        self.config_manager.resume(config.CONFIG_FRONT_ROOMS)
        self.bump_version()
        return {}, 200

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

    @versioned
    @expose("/<id>/pause")
    def pause(self, id):
        job = self.scheduler.get_job(id)
        if job.next_run_time:
            job.pause()
        else:
            job.resume()

    @versioned
    @expose("/<id>/run")
    def run(self, id):
        job = self.scheduler.get_job(id)
        job.func()
