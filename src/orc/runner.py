import argparse
from pathlib import Path

from apscheduler.events import EVENT_JOB_EXECUTED
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask
from gunicorn.app.base import BaseApplication

from orc import api, config
from orc import model as m
from orc.executor import ContextThreadPoolExecutor
from orc.view import VersionManager, bp


def web():
    parser = argparse.ArgumentParser()
    parser.add_argument("--flask-only", action="store_true", help="Run with Flask's dev server instead of gunicorn")
    args = parser.parse_args()

    config_manager = api.ConfigManager()
    version_manager = VersionManager()

    sound_path = (Path(Path(__file__).parent) / "static" / "alert.mp3").resolve().as_posix()
    scheduler = BackgroundScheduler(
        jobstores={"default": SQLAlchemyJobStore(url=config.JOBS_DB)},
        job_defaults={"misfire_grace_time": 30},
    )
    ctx = m.AppContext(config_manager, scheduler, sound_path, version_manager)
    scheduler.add_executor(ContextThreadPoolExecutor(ctx, max_workers=1), "default")

    scheduler.add_listener(lambda e: version_manager.bump_version(), EVENT_JOB_EXECUTED)
    scheduler.start(paused=True)
    api.setup_cal_scheduler(ctx)
    api.setup_iot_scheduler(ctx)

    app = Flask(__name__)
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 604800
    app.orc = ctx
    app.register_blueprint(bp)

    if True or args.flask_only:
        scheduler.resume()
        api.log(api.local_now(), m.LogSource.SYSTEM, "Boot")
        app.run(host="0.0.0.0", port=8000)
        return

    class GunicornApp(BaseApplication):
        def load_config(self):
            self.cfg.set("workers", 1)
            self.cfg.set("threads", 1)
            self.cfg.set("timeout", 120)
            self.cfg.set("loglevel", "warning")
            self.cfg.set("bind", "0.0.0.0:8000")

        def load(self):
            scheduler.resume()
            api.log(api.local_now(), m.LogSource.SYSTEM, "Boot")
            return app

    GunicornApp().run()
