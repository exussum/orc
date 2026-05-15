import os
from pathlib import Path

from apscheduler.events import EVENT_JOB_EXECUTED
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask
from gunicorn.app.base import BaseApplication

import orc as config
from orc import api
from orc import model as m
from orc.view import VersionManager, bp


def web():
    if os.getenv("ENABLED"):
        secrets = api.get_secrets()
        config.config.load(secrets, api.get_hubitat_config(secrets), api.get_chromecast_config(), enabled=True)

    api.init_db()

    config_manager = api.ConfigManager()
    version_manager = VersionManager()

    scheduler = BackgroundScheduler(
        jobstores={
            "default": SQLAlchemyJobStore(url=config.config.jobs_db),
            "memory": MemoryJobStore(),
        },
        job_defaults={"misfire_grace_time": 30},
    )

    ctx = m.AppContext(config_manager, scheduler, (Path(Path(__file__).parent) / "static" / "alert.mp3").resolve().as_posix(), version_manager)
    scheduler.add_executor(api.ContextThreadPoolExecutor(ctx, max_workers=1), "default")
    scheduler.add_listener(lambda e: version_manager.bump_version(), EVENT_JOB_EXECUTED)
    scheduler.start(paused=True)

    api.setup_scheduler(ctx)

    app = Flask(__name__)
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 604800
    app.orc = ctx
    app.register_blueprint(bp)

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
