import os
import signal
import time
from datetime import timedelta
from pathlib import Path

from orc import model as m
from orc.decorators import requires_ctx  # noqa: F401
from orc.locale import Log


def back_on_schedule(ctx: m.AppContext, device: str | None) -> None:
    ctx.api.check_presence(silent=True)
    ctx.api.replay_day(ctx.api.local_now())


def execute_plugin(ctx: m.AppContext, plugin: m.Plugin, device: str | None = None) -> None:
    plugin.func(ctx, device)


def light_test(ctx: m.AppContext, device: str | None) -> None:
    def report(expect_on: bool) -> None:
        wrong = sorted(
            c.what.name
            for c in ctx.api.capture_lights().items
            if c.what not in ctx.config.virtual_devices and (c.state != ctx.model.OFF) != expect_on
        )
        if wrong:
            template = Log.LIGHT_TEST_STILL_OFF if expect_on else Log.LIGHT_TEST_STILL_ON
            ctx.api.log(ctx.model.LogSource.PLUGIN, template.format(names=", ".join(wrong)))

    end = ctx.api.local_now() + timedelta(minutes=10)
    ctx.snapshot_manager.replace_config("light_test", ctx.model.Config(ctx.orc.Light, ctx.model.OFF), end, "light_test")
    time.sleep(10)
    report(expect_on=False)
    ctx.api.dispatch(ctx.model.Config(ctx.orc.Light, ctx.model.ON), force=True)
    time.sleep(10)
    report(expect_on=True)
    ctx.snapshot_manager.resume("light_test", ctx.config.default_config)


def rebuild_jobs(ctx: m.AppContext, device: str | None) -> None:
    ctx.api.rebuild_jobs(ctx)


def reboot(ctx: m.AppContext, device: str | None) -> None:
    os.kill(os.getppid(), signal.SIGTERM)


def reboot_hubitat(ctx: m.AppContext, device: str | None) -> None:
    ctx.api.reboot_hubitat()


def sound_test(ctx: m.AppContext, device: str | None) -> None:
    base = ctx.config.internal_url.rstrip("/") + "/"
    url = f"{base}static/alert.mp3"
    ctx.api.dispatch(ctx.model.Configs(ctx.model.Config(ctx.orc.Chromecast, url)), force=True)
    ctx.api.play_text("audio test")
    alert_path = str(Path(__file__).parent / "static" / "alert.wav")
    for level in (ctx.model.AUDIO_INFO, ctx.model.AUDIO_FATAL):
        ctx.api.play_alert(alert_path, level=level)
