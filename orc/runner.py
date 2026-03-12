import logging
import time
from pathlib import Path

from apscheduler.events import EVENT_JOB_EXECUTED
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from flask import Flask

from orc import api, config
from orc.view import VersionManager, bp


def web():
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    config_manager = api.ConfigManager()
    version_manager = VersionManager()

    sound_file = (Path(Path(__file__).parent) / "static" / "alert.mp3").resolve().as_posix()
    scheduler = BackgroundScheduler()

    api.setup_cal_scheduler(scheduler, config_manager, sound_file)
    api.setup_iot_scheduler(scheduler, config_manager)
    scheduler.add_listener(lambda e: version_manager.bump_version(), EVENT_JOB_EXECUTED)
    scheduler.start()

    app = Flask(__name__)
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config_manager = config_manager
    app.register_blueprint(bp)
    app.scheduler = scheduler
    app.version_manager = VersionManager()

    if config.SSL_KEY and config.SSL_CERT:
        app.run(host="0.0.0.0", port=443, debug=True, ssl_context=(config.SSL_CERT, config.SSL_KEY), use_reloader=False)
    else:
        app.run(host="0.0.0.0", debug=True, use_reloader=False)


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
