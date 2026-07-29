import sys
import traceback
from pathlib import Path

from apscheduler.events import EVENT_JOB_EXECUTED
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from gunicorn.app.base import BaseApplication

import orc as config
from orc import _build, api
from orc import model as m
from orc import plugins
from orc.api import JOBSTORE_DEFAULT, JOBSTORE_MEMORY, ContextThreadPoolExecutor
from orc.locale import Log
from orc.view import OrcFlask, VersionManager, bp


def flask() -> None:
    app, scheduler = _build_app()
    _start_services(scheduler)
    app.run(host="0.0.0.0", port=8000, use_reloader=False)  # nosemgrep: avoid_app_run_with_bad_host


def web() -> None:
    class GunicornApp(BaseApplication):
        def load_config(self) -> None:
            self.cfg.set("workers", 1)
            self.cfg.set("threads", 1)
            self.cfg.set("timeout", 120)
            self.cfg.set("loglevel", "warning")
            self.cfg.set("bind", "0.0.0.0:8000")

        def load(self) -> OrcFlask:
            try:
                app, scheduler = _build_app()
            except Exception:
                traceback.print_exc()
                sys.exit(4)
            _start_services(scheduler)
            return app

    GunicornApp().run()


def _start_services(scheduler: BackgroundScheduler) -> None:
    api.start_mqtt()
    scheduler.resume()
    api.log(api.local_now(), m.LogSource.SYSTEM, Log.BOOT)
    print(f"{api.local_now().isoformat()}: ORC Started", file=sys.stderr, flush=True)


def _build_app() -> tuple[OrcFlask, BackgroundScheduler]:
    secrets = api.fetch_secrets()
    config.config.load(secrets, api.fetch_hubitat_config(secrets))
    api.init_db()

    ctx = _build_scheduler()

    _run_setup(ctx)
    return _build_flask(ctx), ctx.scheduler


def _build_scheduler() -> m.AppContext:
    version_manager = VersionManager()
    scheduler = BackgroundScheduler(
        jobstores={
            JOBSTORE_DEFAULT: SQLAlchemyJobStore(url=config.config.jobs_db),
            JOBSTORE_MEMORY: MemoryJobStore(),
        },
        job_defaults={"misfire_grace_time": 30},
        timezone=config.config.tz,
    )
    ctx = m.AppContext(
        api.snapshot_manager, scheduler, (Path(Path(__file__).parent) / "static" / "alert.wav").resolve().as_posix(), version_manager
    )
    scheduler.add_executor(ContextThreadPoolExecutor(ctx, max_workers=1), JOBSTORE_DEFAULT)
    scheduler.add_listener(lambda e: version_manager.bump_version(), EVENT_JOB_EXECUTED)
    scheduler.start(paused=True)
    return ctx


def _run_setup(ctx: m.AppContext) -> None:
    api.setup_scheduler(ctx)
    plugin_ctx = plugins.build_ctx(ctx)
    for hook in config.config.registry.setup_hooks:
        hook(plugin_ctx)
    api.add_state_provider("Hubitat MQTT", api.mqtt_state_rows)


def _build_flask(ctx: m.AppContext) -> OrcFlask:
    app = OrcFlask(__name__)
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 604800
    app.orc = ctx
    app.jinja_env.globals.update(build_sha=_build.SHA, build_time=_build.BUILD_TIME)
    app.register_blueprint(bp)
    return app
