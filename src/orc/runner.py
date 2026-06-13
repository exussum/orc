import os
import sys
import traceback
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
from orc.apscheduler import JOBSTORE_DEFAULT, JOBSTORE_MEMORY, ContextThreadPoolExecutor
from orc.locale import Log
from orc.view import VersionManager, bp


def flask():
    app, scheduler = _build_app()
    scheduler.resume()
    api.log(api.local_now(), m.LogSource.SYSTEM, Log.BOOT)
    app.run(host="0.0.0.0", port=8000, use_reloader=False)


def web():
    class GunicornApp(BaseApplication):
        def load_config(self):
            self.cfg.set("workers", 1)
            self.cfg.set("threads", 1)
            self.cfg.set("timeout", 120)
            self.cfg.set("loglevel", "warning")
            self.cfg.set("bind", "0.0.0.0:8000")

        def load(self):
            try:
                app, scheduler = _build_app()
            except Exception:
                traceback.print_exc()
                sys.exit(4)
            scheduler.resume()
            api.log(api.local_now(), m.LogSource.SYSTEM, Log.BOOT)
            return app

    GunicornApp().run()


def _build_app():
    if os.getenv("ORC_ENABLED"):
        secrets = api.fetch_secrets()
        config.config.load(secrets, api.fetch_hubitat_config(secrets), api.fetch_config())

    api.init_db()

    config_manager = api.make_config_manager()
    version_manager = VersionManager()

    scheduler = BackgroundScheduler(
        jobstores={
            JOBSTORE_DEFAULT: SQLAlchemyJobStore(url=config.config.jobs_db),
            JOBSTORE_MEMORY: MemoryJobStore(),
        },
        job_defaults={"misfire_grace_time": 30},
    )

    ctx = m.AppContext(
        config_manager, scheduler, (Path(Path(__file__).parent) / "static" / "alert.mp3").resolve().as_posix(), version_manager
    )
    scheduler.add_executor(ContextThreadPoolExecutor(ctx, max_workers=1), JOBSTORE_DEFAULT)
    scheduler.add_listener(lambda e: version_manager.bump_version(), EVENT_JOB_EXECUTED)
    scheduler.start(paused=True)

    api.setup_scheduler(ctx)

    app = Flask(__name__)
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 604800
    app.orc = ctx
    app.register_blueprint(bp)

    return app, scheduler
