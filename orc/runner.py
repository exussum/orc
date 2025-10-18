import time
from collections import deque
from enum import Enum

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from flask import Flask
from flask_admin import BaseView, expose
from flask_admin.base import Admin, AdminIndexView

from orc.control import build_schedule, execute, setup_scheduler
from orc.model import RoutineConfig


def web():
    app = Flask(__name__)
    app.config["FLASK_ADMIN_SWATCH"] = "Slate"
    scheduler = setup_scheduler(BackgroundScheduler())

    class OrcAdminView(AdminIndexView):
        @expose("/")
        def index(self):
            jobs = scheduler.get_jobs()
            return self.render("orc.html", jobs=jobs)

    scheduler.start()
    admin = Admin(app, name="ORChestration", template_mode="bootstrap3", index_view=OrcAdminView(url="/"))
    app.run()


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
