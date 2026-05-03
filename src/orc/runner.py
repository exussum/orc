import logging
import time
from pathlib import Path

from apscheduler.events import EVENT_JOB_EXECUTED
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from flask import Flask

from orc import api, config
from orc import model as m
from orc.view import VersionManager, bp


def web():
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    config_manager = api.ConfigManager()
    version_manager = VersionManager()

    sound_path = (Path(Path(__file__).parent) / "static" / "alert.mp3").resolve().as_posix()
    scheduler = BackgroundScheduler(
        executors={"default": ThreadPoolExecutor(1)},
        job_defaults={"misfire_grace_time": 30},
    )

    api.setup_cal_scheduler(scheduler, config_manager, sound_path)
    api.setup_iot_scheduler(scheduler, config_manager)
    scheduler.add_listener(lambda e: version_manager.bump_version(), EVENT_JOB_EXECUTED)
    scheduler.start()
    api.log(api.local_now(), m.LogSource.SYSTEM, "Boot")

    app = Flask(__name__)
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 604800
    app.config_manager = config_manager
    app.register_blueprint(bp)
    app.scheduler = scheduler
    app.sound_path = sound_path
    app.version_manager = version_manager

    if config.SSL_KEY and config.SSL_CERT:
        app.run(host="0.0.0.0", port=443, debug=True, ssl_context=(config.SSL_CERT, config.SSL_KEY), use_reloader=False, threaded=False)
    else:
        app.run(host="0.0.0.0", debug=True, use_reloader=False, threaded=False)


def worker():
    config_manager = api.ConfigManager()
    sound_path = (Path(Path(__file__).parent) / "static" / "alert.mp3").resolve().as_posix()
    scheduler = BlockingScheduler()
    api.setup_iot_scheduler(scheduler, config_manager)
    api.setup_cal_scheduler(scheduler, config_manager, sound_path)

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
