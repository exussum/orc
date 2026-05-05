import time
from pathlib import Path

from apscheduler.events import EVENT_JOB_EXECUTED
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from flask import Flask
from gunicorn.app.base import BaseApplication

from orc import api, config
from orc import model as m
from orc.view import VersionManager, bp


def web():
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

    app = Flask(__name__)
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 604800
    app.config_manager = config_manager
    app.register_blueprint(bp)
    app.scheduler = scheduler
    app.sound_path = sound_path
    app.version_manager = version_manager

    class GunicornApp(BaseApplication):
        def load_config(self):
            self.cfg.set("workers", 1)
            self.cfg.set("threads", 1)
            self.cfg.set("loglevel", "warning")
            if config.SSL_KEY and config.SSL_CERT:
                self.cfg.set("bind", "0.0.0.0:443")
                self.cfg.set("certfile", config.SSL_CERT)
                self.cfg.set("keyfile", config.SSL_KEY)
            else:
                self.cfg.set("bind", "0.0.0.0:8000")

        def load(self):
            scheduler.start()
            api.log(api.local_now(), m.LogSource.SYSTEM, "Boot")
            return app

    GunicornApp().run()


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
