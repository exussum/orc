import os
import signal
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from orc import model as m
from orc.decorators import requires_ctx  # noqa: F401


def back_on_schedule(ctx: m.AppContext) -> None:
    ctx.api.check_presence(silent=True)
    ctx.api.replay_day(ctx.api.local_now())


def execute_plugin(ctx: m.AppContext, id: str, **params: Any) -> None:
    ctx.config.plugins[id].func(ctx, **params)


def light_test(ctx: m.AppContext) -> None:
    end = ctx.api.local_now() + timedelta(minutes=10)
    ctx.snapshot_manager.replace_config("light_test", ctx.model.Config(ctx.orc.Light, ctx.model.OFF), end, "light_test")
    ctx.api.dispatch(ctx.model.Config(ctx.orc.Light, ctx.model.ON), force=True)
    time.sleep(30)
    ctx.snapshot_manager.resume("light_test", ctx.config.default_config)


def rebuild_jobs(ctx: m.AppContext) -> None:
    ctx.api.rebuild_jobs(ctx)


def reboot(ctx: m.AppContext) -> None:
    os.kill(os.getppid(), signal.SIGTERM)


def reboot_hubitat(ctx: m.AppContext) -> None:
    ctx.api.reboot_hubitat()


def sound_test(ctx: m.AppContext) -> None:
    base = ctx.config.internal_url.rstrip("/") + "/"
    url = f"{base}static/alert.mp3"
    ctx.api.dispatch(ctx.model.Configs(ctx.model.Config(ctx.orc.Chromecast, url)), force=True)
    ctx.api.play_text("audio test")
    alert_path = str(Path(__file__).parent / "static" / "alert.wav")
    for level in (ctx.model.AUDIO_INFO, ctx.model.AUDIO_FATAL):
        ctx.api.play_alert(alert_path, level=level)
