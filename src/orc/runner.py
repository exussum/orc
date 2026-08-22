import sys
import traceback

from apscheduler.events import EVENT_JOB_EXECUTED
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from gunicorn.app.base import BaseApplication

import orc as config
from orc import _build, api
from orc import model as m
from orc.api import JOBSTORE_DEFAULT, JOBSTORE_MEMORY
from orc.dal.scheduler import ContextThreadPoolExecutor, set_scheduler
from orc.locale import Log
from orc.view import OrcFlask, VersionManager, bp


def flask() -> None:
    app = _build_app()
    _start_services(app.orc)
    app.run(host="0.0.0.0", port=config.config.settings.port, use_reloader=False)  # nosemgrep: avoid_app_run_with_bad_host


def web() -> None:
    class GunicornApp(BaseApplication):
        def load_config(self) -> None:
            self.cfg.set("workers", 1)
            self.cfg.set("threads", 1)
            self.cfg.set("timeout", 120)
            self.cfg.set("loglevel", "warning")
            self.cfg.set("bind", f"0.0.0.0:{config.config.settings.port}")

        def load(self) -> OrcFlask:
            try:
                app = _build_app()
            except Exception:
                traceback.print_exc()
                sys.exit(4)
            _start_services(app.orc)
            return app

    GunicornApp().run()


def _start_services(ctx: m.AppContext) -> None:
    api.wire_buttons(ctx)
    api.wire_external_log()
    config.config.providers.mqtt.start()
    ctx.scheduler.resume()
    api.log(m.LogSource.SYSTEM, Log.BOOT)
    print(f"{api.local_now().isoformat()}: ORC Started", file=sys.stderr, flush=True)


def _build_app() -> OrcFlask:
    secrets = config.config.providers.secrets.fetch_secrets()
    config.config.load(secrets, config.config.providers.mqtt.fetch_hubitat_config(secrets))
    api.init_db()

    scheduler = _build_scheduler()
    set_scheduler(scheduler)
    ctx = m.AppContext(api.snapshot_manager, scheduler, VersionManager())
    config.config.registry.ctx = ctx
    scheduler.add_executor(ContextThreadPoolExecutor(ctx), JOBSTORE_DEFAULT)
    scheduler.add_listener(lambda e: ctx.version_manager.bump_version(), EVENT_JOB_EXECUTED)
    scheduler.start(paused=True)

    api.setup_scheduler(ctx)
    for hook in config.config.registry.setup_hooks:
        hook(ctx)
    return _build_flask(ctx)


def _build_scheduler() -> BackgroundScheduler:
    return BackgroundScheduler(
        jobstores={
            JOBSTORE_DEFAULT: SQLAlchemyJobStore(url=config.config.settings.jobs_db),
            JOBSTORE_MEMORY: MemoryJobStore(),
        },
        job_defaults={"misfire_grace_time": 30},
        timezone=config.config.settings.tz,
    )


def _build_flask(ctx: m.AppContext) -> OrcFlask:
    app = OrcFlask(__name__)
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 604800
    app.orc = ctx
    app.jinja_env.globals.update(build_sha=_build.SHA, build_time=_build.BUILD_TIME)
    app.register_blueprint(bp)
    for plugin, namespace, plugin_bp in config.config.registry.blueprints:
        app.register_blueprint(plugin_bp, url_prefix=f"/api/{plugin}/{namespace}")
    return app
