import argparse
import os
import subprocess
import sys
import time
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from urllib.parse import quote

import orc_queue as oq
from gunicorn.app.base import BaseApplication

from orc import _build, api, config
from orc import model as m
from orc.dal import broker, net
from orc.dal.scheduler import Scheduler
from orc.interfaces import inbound
from orc.interfaces.web import OrcFlask, bp
from orc.kernel.loader import check_secrets
from orc.log import Log, Message


@dataclass
class Boot:
    log: Log = field(default_factory=lambda: Log(api.local_now, api.notify))
    trigger: m.Trigger = field(default_factory=lambda: m.System("boot", log_source=m.LogSource.SYSTEM))
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
        self.cfg.set("bind", f"0.0.0.0:{config.settings.port}")

    def load(self) -> OrcFlask:
        _start_services(self._app.orc, self._boot)
        return self._app


def flask() -> None:
    subprocess.run(["tailwindcss", "-i", "src/css/tailwind.src.css", "-o", "src/orc/static/tailwind.min.css", "--minify"], check=True)
    subprocess.run([sys.executable, "scripts/build_mac.py"], check=True)
    boot = Boot()
    app = _build_app(boot)
    _start_services(app.orc, boot)
    app.run(host="0.0.0.0", port=config.settings.port, use_reloader=False)  # nosemgrep: avoid_app_run_with_bad_host


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
    config.load(m.Secrets(), {})
    fetched = config.providers.secrets.fetch_secrets()
    needs = config.secret_needs
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
        boot.entry, boot.started = boot.log.write(Message.BOOT, m.System("boot", log_source=m.LogSource.SYSTEM)), start
    boot.entry.add(m.LogSource.SYSTEM, Message.BOOT_STEP.format(name=name, seconds=f"{time.perf_counter() - start:.1f}"))


def _start_services(ctx: m.AppContext, boot: Boot) -> None:
    step = partial(_step, boot)
    with step("scheduler setup"):
        setup_scheduler(ctx, boot.trigger)
    for hook in config.registry.setup_hooks:
        with step(f"{hook.__module__} setup"):
            hook(ctx)
    with step("mqtt start"):
        config.providers.broker.register(config.providers.adapter)
        broker.add_status_listener(api.log_external)
        config.providers.broker.start(inbound.on_message)
    with step("ble setup"):
        net.presence.start(config.tags, config.settings.tz, inbound.on_heard)
        if net.presence.tagged:
            ctx.scheduler.service(net.presence.listen, name="ble-listener")
    with step("scheduler start"):
        ctx.scheduler.start(ctx)
    with step("scheduler resume"):
        ctx.scheduler.resume()
    with step("presence check"):
        api.schedule_presence_check(m.Cron("presence", log_source=m.LogSource.SYSTEM))
    with step("tag key check"):
        api.schedule_tag_key_refresh(m.Cron("findhub", log_source=m.LogSource.SYSTEM))
    assert boot.entry is not None and boot.started is not None
    boot.log.add(boot.entry, m.LogSource.SYSTEM, Message.BOOT_TOTAL.format(seconds=f"{time.perf_counter() - boot.started:.1f}"))
    print(f"{api.local_now().isoformat()}: ORC Started", file=sys.stderr, flush=True)


def _build_app(boot: Boot) -> OrcFlask:
    step = partial(_step, boot)
    with step("config load"):
        config.load()
    with step("database init"):
        api.init_db()
        api.load_durations()

    ctx = m.AppContext(Scheduler(config.config.settings.jobs_db, config.config.settings.tz), api.runtime(), log=boot.log)
    api.set_ctx(ctx)
    return _build_flask(ctx)


def _build_flask(ctx: m.AppContext) -> OrcFlask:
    app = OrcFlask("orc")
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 3600
    app.orc = ctx
    app.jinja_env.globals.update(build_sha=_build.SHA, build_time=_build.BUILD_TIME)
    app.register_blueprint(bp)
    for plugin, namespace, plugin_bp in config.registry.blueprints:
        app.register_blueprint(plugin_bp, url_prefix=f"/api/{plugin}/{namespace}")
    return app
