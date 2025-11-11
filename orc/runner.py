import random
import time
from zoneinfo import ZoneInfo

from apscheduler.events import EVENT_JOB_EXECUTED
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from flask import Flask
from flask_admin.base import Admin

from orc import api
from orc.model import RoutineConfig
from orc.view import OrcAdminView


def web():
    app = Flask(__name__)
    app.config["FLASK_ADMIN_SWATCH"] = "cyborg"

    scheduler = api.setup_scheduler(BackgroundScheduler())
    scheduler.add_listener(OrcAdminView.bump_version, EVENT_JOB_EXECUTED)
    scheduler.start()

    admin = Admin(
        app, name="ORChestration", template_mode="bootstrap4", index_view=OrcAdminView(scheduler=scheduler, url="/")
    )
    app.run(host="0.0.0.0")


def worker():
    scheduler = ctrl.setup_scheduler(BlockingScheduler())

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass


def test():
    for when, e in sorted(ctrl.build_schedule(), key=lambda x: x[0]):
        print(e)
        time.sleep(1)
        ctrl.execute(e)
        time.sleep(1)
