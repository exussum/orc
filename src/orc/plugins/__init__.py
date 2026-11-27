import os
import signal
import time
from dataclasses import replace
from datetime import timedelta

from orc_engine import model as em

from orc import model as m
from orc.decorators import connected  # noqa: F401
from orc.log import Message

_LIGHT_TEST_WINDOW = timedelta(minutes=10)
_LIGHT_TEST_SETTLE_SECONDS = 10


def back_on_schedule(ctx: m.AppContext, device: str | None, *, trigger: m.Trigger) -> None:
    ctx.api.check_presence(trigger)
    ctx.api.replay_day(ctx.api.local_now(), trigger)


def execute_plugin(ctx: m.AppContext, plugin: m.CallablePlugin, device: str | None, *, trigger: m.Trigger) -> None:
    plugin.func(ctx, device, trigger=trigger)


def light_test(ctx: m.AppContext, device: str | None, *, trigger: m.Trigger) -> None:
    def report(expect_on: bool) -> None:
        wrong = sorted(
            device.name
            for c in ctx.api.capture_lights()
            if (device := c.subject.one()) not in ctx.config.virtual_devices and (c.value != m.OFF) != expect_on
        )
        if wrong:
            template = Message.LIGHT_TEST_STILL_OFF if expect_on else Message.LIGHT_TEST_STILL_ON
            ctx.log.write(template.format(names=", ".join(wrong)), replace(trigger, log_source=m.LogSource.PLUGIN))

    end = ctx.api.local_now() + _LIGHT_TEST_WINDOW
    ctx.api.override_scene("light_test", (em.Command(m.Devices(ctx.config.devices.Light), m.OFF),), end, "light_test", trigger)
    time.sleep(_LIGHT_TEST_SETTLE_SECONDS)
    report(expect_on=False)
    ctx.api.dispatch((em.Command(m.Devices(ctx.config.devices.Light), m.ON),), force=True, trigger=trigger)
    time.sleep(_LIGHT_TEST_SETTLE_SECONDS)
    report(expect_on=True)
    ctx.api.restore_scene("light_test", ctx.config.default_config.commands, trigger)


def rebuild_jobs(ctx: m.AppContext, device: str | None, *, trigger: m.Trigger) -> None:
    ctx.api.rebuild_jobs(trigger)


def reboot(ctx: m.AppContext, device: str | None, *, trigger: m.Trigger) -> None:
    os.kill(os.getppid(), signal.SIGTERM)


def sound_test(ctx: m.AppContext, device: str | None, *, trigger: m.Trigger) -> None:
    for severity in m.Alarm:
        ctx.api.alert(severity, text=Message.SOUND_TEST_TEXT, trigger=trigger)
