import random
import time
from zoneinfo import ZoneInfo

from apscheduler.events import EVENT_JOB_EXECUTED
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from flask import Flask
from flask_admin.base import Admin

from orc import api
from orc.model import Routine
from orc.view import ButtonView, ScheduleView, VersionedView


def web():
    app = Flask(__name__)
    app.config["FLASK_ADMIN_SWATCH"] = "cyborg"
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    config_manager = api.ConfigManager()
    scheduler = api.setup_scheduler(BackgroundScheduler(), config_manager)
    scheduler.add_listener(lambda e: VersionedView.bump_version(), EVENT_JOB_EXECUTED)
    scheduler.start()

    admin = Admin(
        app,
        name="ORChestration",
        template_mode="bootstrap4",
        index_view=ButtonView(config_manager=config_manager, url="/", name="Remote"),
    )
    admin.add_view(ScheduleView(config_manager=config_manager, scheduler=scheduler, url="/schedule", name="Schedule"))
    app.run(host="0.0.0.0")


def worker():
    config_manager = api.ConfigManager()
    scheduler = ctrl.setup_scheduler(BlockingScheduler(), config_manager)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass


def test():
    for when, e in sorted(api.get_schedule(api.ConfigManager()), key=lambda x: x[0]):
        print(e)
        time.sleep(1)
        api.execute(e)
        time.sleep(1)
