import os
import subprocess
import sys
import traceback
from urllib.parse import urlparse

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
    subprocess.run(["tailwindcss", "-i", "src/css/tailwind.src.css", "-o", "src/orc/static/tailwind.min.css", "--minify"], check=True)
    app = _build_app()
    _start_services(app.orc)
    app.run(host="0.0.0.0", port=config.config.settings.port, use_reloader=False)  # nosemgrep: avoid_app_run_with_bad_host


def _split_stderr() -> None:
    """Python keeps stderr via a private dup; fd 2 itself goes to /dev/null.

    Native libraries (onnxruntime, ALSA, JACK, …) spew to fd 2 directly and can't be
    muted per-thread, while everything Python-side (print, logging, tracebacks) goes
    through sys.stderr. Splitting them once at startup silences all C noise for good
    without ever redirecting the stream Python logs to.
    """
    saved = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 2)
    os.close(devnull)
    sys.stderr = os.fdopen(saved, "w", buffering=1)


def web() -> None:
    _split_stderr()
    try:
        app = _build_app()
    except Exception:
        traceback.print_exc()
        sys.exit(4)

    class GunicornApp(BaseApplication):
        def load_config(self) -> None:
            self.cfg.set("workers", 1)
            self.cfg.set("threads", 1)
            self.cfg.set("timeout", 120)
            self.cfg.set("loglevel", "warning")
            self.cfg.set("bind", f"0.0.0.0:{config.config.settings.port}")

        def load(self) -> OrcFlask:
            _start_services(app.orc)
            return app

    GunicornApp().run()


def _start_services(ctx: m.AppContext) -> None:
    # Start the scheduler and its services here, after gunicorn has forked the worker:
    # the scheduler's thread and the mqtt network loops don't survive the fork, so they
    # must start in the worker, not in _build_app (which runs pre-fork in web()).
    ctx.scheduler.start(paused=True)
    api.setup_scheduler(ctx)
    for hook in config.config.registry.setup_hooks:
        hook(ctx)
    api.wire_buttons(ctx)
    api.wire_external_log()
    config.config.providers.mqtt.start()
    api.start_ble_listener()
    ctx.scheduler.resume()
    api.schedule_presence_check()
    api.log(m.LogSource.SYSTEM, Log.BOOT)
    print(f"{api.local_now().isoformat()}: ORC Started", file=sys.stderr, flush=True)


def _build_app() -> OrcFlask:
    # bootstrap parse with empty inputs so the provider modules are known, then
    # reload with the real secrets and hub device map they fetch
    config.config.load(m.Secrets(), {})
    secrets = config.config.providers.secrets.fetch_secrets()
    config.config.load(secrets, config.config.providers.mqtt.fetch_hubitat_config(secrets))
    api.init_db()

    scheduler = _build_scheduler()
    set_scheduler(scheduler)
    ctx = m.AppContext(scheduler, VersionManager())
    api.set_ctx(ctx)
    scheduler.add_executor(ContextThreadPoolExecutor(ctx), JOBSTORE_DEFAULT)
    scheduler.add_listener(lambda e: ctx.version_manager.bump_version(), EVENT_JOB_EXECUTED)
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
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 3600
    app.orc = ctx
    internal_host = urlparse(config.config.settings.base_url).hostname if config.config.settings.base_url else None
    app.jinja_env.globals.update(build_sha=_build.SHA, build_time=_build.BUILD_TIME, internal_host=internal_host)
    app.register_blueprint(bp)
    for plugin, namespace, plugin_bp in config.config.registry.blueprints:
        app.register_blueprint(plugin_bp, url_prefix=f"/api/{plugin}/{namespace}")
    return app
