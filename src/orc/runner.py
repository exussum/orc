import argparse
import os
import subprocess
import sys
import time
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from urllib.parse import quote, urlparse

from gunicorn.app.base import BaseApplication

import orc as config
from orc import _build, api
from orc import model as m
from orc.dal.mqtt import paho as mqtt
from orc.dal.scheduler import Scheduler
from orc.kernel.loader import check_secrets
from orc.locale import Log
from orc.view import OrcFlask, bp


@dataclass
class Boot:
    entry: m.LogEntry | None = None
    started: float | None = None


class GunicornApp(BaseApplication):
    def __init__(self, app: OrcFlask, boot: Boot) -> None:
        self._app = app
        self._boot = boot
        super().__init__()

    def load_config(self) -> None:
        self.cfg.set("workers", 1)
        self.cfg.set("threads", 1)
        self.cfg.set("timeout", 120)
        self.cfg.set("loglevel", "warning")
        self.cfg.set("bind", f"0.0.0.0:{config.config.settings.port}")

    def load(self) -> OrcFlask:
        _start_services(self._app.orc, self._boot)
        return self._app


def flask() -> None:
    subprocess.run(["tailwindcss", "-i", "src/css/tailwind.src.css", "-o", "src/orc/static/tailwind.min.css", "--minify"], check=True)
    boot = Boot()
    app = _build_app(boot)
    _start_services(app.orc, boot)
    app.run(host="0.0.0.0", port=config.config.settings.port, use_reloader=False)  # nosemgrep: avoid_app_run_with_bad_host


def secrets() -> None:
    parser = argparse.ArgumentParser(
        prog="orc-secrets",
        description="Fetch the secrets a config names and check each one the way startup does: "
        "every secret a selected provider, plugin or tag line declares must be set and well-formed. "
        "BWS_ACCESS_TOKEN is the Bitwarden machine token itself, not a URL.",
    )
    parser.add_argument("config", type=Path, help="config.orc, or the directory holding it")
    path = parser.parse_args().config
    os.environ["ORC_CONFIG_DIR"] = str(path.parent if path.is_file() else path)
    if token := os.environ.get("BWS_ACCESS_TOKEN"):
        os.environ["BWS_ACCESS_TOKEN"] = "data:," + quote(token, safe="")
    config.config.load(m.Secrets(), {})
    fetched = config.config.providers.secrets.fetch_secrets()
    needs = config.config.secret_needs
    problems = check_secrets(fetched, needs)
    for name in sorted(needs):
        print(f"{name:<40} {f'bad: {problems[name]}' if name in problems else 'ok'}")
    for name in sorted(set(fetched.other) - set(needs)):
        print(f"{name:<40} unused")
    if problems:
        sys.exit(1)


def web() -> None:
    _split_stderr()
    boot = Boot()
    try:
        app = _build_app(boot)
    except Exception:
        traceback.print_exc()
        sys.exit(4)
    GunicornApp(app, boot).run()


def _split_stderr() -> None:
    saved = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 2)
    os.close(devnull)
    sys.stderr = os.fdopen(saved, "w", buffering=1)


@contextmanager
def _step(boot: Boot, name: str) -> Iterator[None]:
    start = time.perf_counter()
    yield
    if boot.entry is None:
        boot.entry, boot.started = api.log(m.LogSource.SYSTEM, Log.BOOT, m.System("boot")), start
    boot.entry.add(m.LogSource.SYSTEM, Log.BOOT_STEP.format(name=name, seconds=f"{time.perf_counter() - start:.1f}"))


def _start_services(ctx: m.AppContext, boot: Boot) -> None:
    step = partial(_step, boot)
    with step("scheduler start"):
        ctx.scheduler.start(ctx)
        api.setup_scheduler(ctx)
    for hook in config.config.registry.setup_hooks:
        with step(hook.__module__):
            hook(ctx)
    with step("mqtt"):
        api.register_adapter(config.config.providers.adapter)
        mqtt.start()
    with step("ble"):
        api.start_ble_listener()
    with step("scheduler resume"):
        ctx.scheduler.resume()
    with step("presence check"):
        api.schedule_presence_check(m.Cron("presence"))
    assert boot.entry is not None and boot.started is not None
    boot.entry.add(m.LogSource.SYSTEM, Log.BOOT_TOTAL.format(seconds=f"{time.perf_counter() - boot.started:.1f}"))
    print(f"{api.local_now().isoformat()}: ORC Started", file=sys.stderr, flush=True)


def _build_app(boot: Boot) -> OrcFlask:
    step = partial(_step, boot)
    with step("config"):
        config.config.load()
    with step("database"):
        api.init_db()

    ctx = m.AppContext(Scheduler(config.config.settings.jobs_db, config.config.settings.tz), api.runtime())
    api.set_ctx(ctx)
    return _build_flask(ctx)


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
