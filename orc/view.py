import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import wraps
from typing import List
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from flask import request
from flask_admin import expose
from flask_admin.base import AdminIndexView

from orc import config
from orc import control as ctrl


@dataclass
class SnapShot:
    routine: List[config.LightConfig]
    when: datetime


class OrcAdminView(AdminIndexView):
    version = str(random.random())
    snapshot = None
    theme_override = None

    def __init__(self, url, scheduler):
        super(AdminIndexView, self).__init__(url=url)
        self.scheduler = scheduler

    @classmethod
    def bump_version(cls):
        cls.version = str(random.random())

    @staticmethod
    def versioned(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not request.headers.get("orc-version") == self.version:
                return {"version": self.version}, 412
            func(*args, **kwargs)
            self.version = str(random.random())
            return {"version": self.version}, 200

        return wrapper

    def _non_cron_jobs(self):
        now = datetime.now(tz=ZoneInfo("America/New_York"))
        return [
            e for e in self.scheduler.get_jobs() if not isinstance(e.trigger, CronTrigger) and e.trigger.run_date > now
        ]

    @expose("/")
    def index(self):
        jobs = sorted(self._non_cron_jobs(), key=lambda e: e.trigger.run_date)
        return self.render("orc.html", version=self.version, jobs=jobs), 200, {"Cache-control": "no-store"}

    @expose("/tv_mode_on")
    def tv_mode_on(self):
        now = datetime.now()
        if not self.snapshot or now - self.snapshot.when > timedelta(hours=4):
            self.snapshot = SnapShot(when=now, routine=ctrl.capture_lights())

        for e in self._non_cron_jobs():
            e.pause()

        ctrl.execute(config.TV_LIGHTS_CONFIG)
        self.bump_version()
        return {}, 200

    @expose("/tv_mode_off")
    def tv_mode_off(self):
        if self.snapshot and datetime.now() - self.snapshot.when <= timedelta(hours=4):
            ctrl.execute(self.snapshot.routine)
            self.snapshot = None

            for e in self._non_cron_jobs():
                e.resume()

            self.bump_version()
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
