import os
import signal
import time
from datetime import timedelta

from orc import model as m
from orc.decorators import requires_ctx  # noqa: F401
from orc.kernel import engine
from orc.locale import Log

_LIGHT_TEST_WINDOW = timedelta(minutes=10)
_LIGHT_TEST_SETTLE_SECONDS = 10


def back_on_schedule(ctx: m.AppContext, device: str | None, *, entry: m.LogEntry) -> None:
    ctx.api.check_presence(silent=True)
    ctx.api.replay_day(ctx.api.local_now(), entry)


def execute_plugin(ctx: m.AppContext, plugin: m.CallablePlugin, device: str | None, *, entry: m.LogEntry) -> None:
    plugin.func(ctx, device, entry=entry)


def light_test(ctx: m.AppContext, device: str | None, *, entry: m.LogEntry) -> None:
    def report(expect_on: bool) -> None:
        wrong = sorted(
            device.name
            for c in ctx.api.capture_lights()
            if (device := c.channel.one()) not in ctx.config.virtual_devices and (c.value != m.OFF) != expect_on
        )
        if wrong:
            template = Log.LIGHT_TEST_STILL_OFF if expect_on else Log.LIGHT_TEST_STILL_ON
            ctx.api.log(m.LogSource.PLUGIN, template.format(names=", ".join(wrong)))

    end = ctx.api.local_now() + _LIGHT_TEST_WINDOW
    ctx.engine.override_scene(ctx, "light_test", (engine.Command(m.Devices(ctx.orc.Light), m.OFF),), end, "light_test", entry)
    time.sleep(_LIGHT_TEST_SETTLE_SECONDS)
    report(expect_on=False)
    ctx.api.dispatch((engine.Command(ctx.orc.Light, m.ON),), force=True, entry=entry)
    time.sleep(_LIGHT_TEST_SETTLE_SECONDS)
    report(expect_on=True)
    ctx.engine.restore_scene(ctx, "light_test", ctx.config.default_config.commands, entry)


def rebuild_jobs(ctx: m.AppContext, device: str | None, *, entry: m.LogEntry) -> None:
    ctx.api.rebuild_jobs(ctx)


def reboot(ctx: m.AppContext, device: str | None, *, entry: m.LogEntry) -> None:
    os.kill(os.getppid(), signal.SIGTERM)


def reboot_hubitat(ctx: m.AppContext, device: str | None, *, entry: m.LogEntry) -> None:
    ctx.api.reboot_hubitat()


def sound_test(ctx: m.AppContext, device: str | None, *, entry: m.LogEntry) -> None:
    for severity in m.Alarm:
        ctx.api.alert(severity, text=Log.SOUND_TEST_TEXT, entry=entry)
