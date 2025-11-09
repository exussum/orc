import random
from datetime import datetime, timedelta
from typing import List
from functools import wraps
from orc import config
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from flask import request
from flask_admin import expose
from flask_admin.base import AdminIndexView

from dataclasses import dataclass

from orc import control as ctrl


@dataclass
class SnapShot:
    routine: List[config.LightConfig]
    when: datetime


class OrcAdminView(AdminIndexView):
    version = str(random.random())
    snapshot = None

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
        now = datetime.now(tz=ZoneInfo("America/New_York"))
        jobs = [e for e in scheduler.get_jobs() if not isinstance(e.trigger, CronTrigger) and e.trigger.run_date > now]
        jobs = sorted(jobs, key=lambda e: e.trigger.run_date)
        return self.render("orc.html", version=self.version, jobs=jobs), 200, {"Cache-control": "no-store"}

    @expose("/tv_mode_on")
    def tv_mode_on(self):
        now = datetime.now()
        if not OrcAdminView.snapshot or now - OrcAdminView.snapshot.when > timedelta(hours=4):
            OrcAdminView.snapshot = SnapShot(when=now, routine=ctrl.capture_lights())
        ctrl.execute(config.TV_LIGHTS_CONFIG)
        self.bump_version()
        return {}, 200

    @expose("/tv_mode_off")
    def tv_mode_off(self):
        if OrcAdminView.snapshot and datetime.now() - OrcAdminView.snapshot.when <= timedelta(hours=4):
            ctrl.execute(OrcAdminView.snapshot.routine)
            OrcAdminView.snapshot = None
            self.bump_version()
        return {}, 200

    @versioned
    @expose("/<id>/pause")
    def pause(self, id):
        job = scheduler.get_job(id)
        if job.next_run_time:
            job.pause()
        else:
            job.resume()

    @versioned
    @expose("/<id>/run")
    def run(self, id):
        job = scheduler.get_job(id)
        job.func()
