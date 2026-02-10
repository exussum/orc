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
from orc.view import VersionManager, bp


def web():
    config_manager = api.ConfigManager()
    version_manager = VersionManager()
    scheduler = api.setup_scheduler(BackgroundScheduler(), config_manager)
    scheduler.add_listener(lambda e: scheduler.bump_version(), EVENT_JOB_EXECUTED)
    scheduler.start()

    app = Flask(__name__)
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.register_blueprint(bp)
    app.scheduler = scheduler
    app.config_manager = config_manager
    app.version_manager = VersionManager()
    app.run(host="0.0.0.0")


def worker():
    config_manager = api.ConfigManager()
    scheduler = api.setup_scheduler(BlockingScheduler(), config_manager)

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
