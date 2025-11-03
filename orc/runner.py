import random
import time
from collections import deque
from datetime import datetime
from enum import Enum
from functools import wraps
from zoneinfo import ZoneInfo

from apscheduler.events import EVENT_JOB_EXECUTED
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask, request
from flask_admin import BaseView, expose
from flask_admin.base import Admin, AdminIndexView

from orc.control import build_schedule, execute, setup_scheduler
from orc.model import RoutineConfig


def web():
    app = Flask(__name__)
    app.config["FLASK_ADMIN_SWATCH"] = "Cyborg"
    scheduler = setup_scheduler(BackgroundScheduler())

    class OrcAdminView(AdminIndexView):
        version = str(random.random())

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
            jobs = [
                e for e in scheduler.get_jobs() if not isinstance(e.trigger, CronTrigger) and e.trigger.run_date > now
            ]
            jobs = sorted(jobs, key=lambda e: e.trigger.run_date)
            return self.render("orc.html", version=self.version, jobs=jobs), 200, {"Cache-control": "no-store"}

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

    def bump_version(event):
        OrcAdminView.version = str(random.random())

    scheduler.add_listener(bump_version, EVENT_JOB_EXECUTED)
    scheduler.start()
    admin = Admin(app, name="ORChestration", template_mode="bootstrap4", index_view=OrcAdminView(url="/"))
    app.run(host="0.0.0.0")


def worker():
    scheduler = setup_scheduler(BlockingScheduler())

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass


def test():
    for when, e in sorted(build_schedule(), key=lambda x: x[0]):
        print(e)
        time.sleep(1)
        execute(e)
        time.sleep(1)
