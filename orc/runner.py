import random
import time
from collections import deque
from enum import Enum

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

        @expose("/")
        def index(self):
            jobs = [e for e in scheduler.get_jobs() if not isinstance(e.trigger, CronTrigger)]
            jobs = sorted(jobs, key=lambda e: e.trigger.run_date)
            return self.render("orc.html", version=self.version, jobs=jobs), 200, {"Cache-control": "no-store"}

        @expose("/<id>/run")
        def run(self, id):
            if not request.headers["orc-version"] == self.version:
                return {"version": self.version}, 412

            job = scheduler.get_job(id)
            job.func()

            self.version = str(random.random())
            return {"version": self.version}, 200

        @expose("/<id>/pause")
        def run(self, id):
            if not request.headers["orc-version"] == self.version:
                return {"version": self.version}, 412

            job = scheduler.get_job(id)
            if job.next_run_time:
                job.pause()
            else:
                job.resume()

            self.version = str(random.random())
            return {"version": self.version}, 200

    scheduler.start()
    admin = Admin(app, name="ORChestration", template_mode="bootstrap4", index_view=OrcAdminView(url="/"))
    app.run(debug=True)


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
