import random
from dataclasses import dataclass
from datetime import datetime, timedelta, date
from functools import wraps
from typing import List
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from flask import request
from flask_admin import expose
from flask_admin.base import AdminIndexView

from orc import config
from orc import api


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
        return self.render("orc.html", version=self.version, jobs=jobs), 200, {"Cache-control": "no-store"}

    @expose("/tv_mode_on")
    def tv_mode_on(self):
        now = datetime.now(tz=ZoneInfo("America/New_York"))
        if now.hour <= 20:
            end = now + timedelta(hours=4)
        else:
            end = now.replace(hour=23, minute=59)

        api.pause_jobs(end)
        self.config_manager.replace_config(config.CONFIG_TV_LIGHTS, end)
        self.bump_version()
        return {}, 200

    @expose("/tv_mode_off")
    def tv_mode_off(self):
        self.config_manager.resume(config.CONFIG_FRONT_ROOMS)
        api.resume_jobs()
        self.bump_version()
        return {}, 200

    @expose("/activate_theme")
    def activate_theme(self):
        self.config_manager.set_theme_override("away", date(2025,11,14), date(2025,11,14))
        self.scheduler.remove_all_jobs()
        api.setup_scheduler(self.scheduler, self.config_manager)
        return {}, 200

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
