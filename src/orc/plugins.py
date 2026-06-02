from __future__ import annotations

import os
import signal
import sys
from dataclasses import dataclass
from datetime import timedelta
from types import ModuleType
from typing import TYPE_CHECKING

from apscheduler.schedulers.base import BaseScheduler
from apscheduler.triggers.date import DateTrigger
from flask import request

from orc.apscheduler import JOBSTORE_MEMORY, requires_ctx
from orc.locale import Log

_SENSOR_ID_ENTRANCE = 16
_SENSOR_EVENT_ACTIVE = "active"

if TYPE_CHECKING:
    from orc import Config as OrcConfig
    from orc.api import ConfigManager
    from orc.model import DeviceEnum


@dataclass
class PluginCtx:
    config_manager: ConfigManager
    Light: type[DeviceEnum]
    Sound: type[DeviceEnum]
    config: OrcConfig
    api: ModuleType
    model: ModuleType
    scheduler: BaseScheduler | None = None


def build_ctx(config_manager, scheduler=None):
    from orc import Light, Sound, api, config, model

    return PluginCtx(
        config_manager=config_manager,
        Light=Light,
        Sound=Sound,
        config=config,
        api=api,
        model=model,
        scheduler=scheduler,
    )


def execute_plugin(config_manager, id):
    ctx = build_ctx(config_manager)
    getattr(sys.modules[__name__], ctx.config.plugins[id])(ctx)


def reboot(ctx):
    os.kill(os.getppid(), signal.SIGTERM)


def light_test(ctx):
    end = ctx.api.local_now() + timedelta(minutes=10)
    ctx.config_manager.replace_config(ctx.model.Config(ctx.Light, ctx.config.OFF), end)
    ctx.api.light_test()
    ctx.config_manager.resume(ctx.config.default_config)


def back_on_schedule(ctx):
    ctx.api.replay_day(ctx.config_manager, ctx.api.local_now())


def all_lights_on(ctx):
    ctx.api.execute(ctx.model.Configs(ctx.model.Config(ctx.Light, ctx.config.ON), ctx.model.Config(ctx.Light, 100)))


def all_lights_off(ctx):
    ctx.api.execute(
        ctx.model.Configs(
            ctx.model.Config(ctx.Light, ctx.config.OFF),
        )
    )


def video_conference(ctx):
    ctx.api.execute(
        ctx.model.Configs(
            ctx.model.Config(ctx.Light.OFFICE_TABLE, 5),
            ctx.model.Config(ctx.Light.OFFICE_FLOOR, ctx.config.ON),
            ctx.model.Config(ctx.Light.OFFICE_DESK, 50),
        )
    )


def silence(ctx):
    ctx.api.execute(
        ctx.model.Configs(
            ctx.model.Config(ctx.Sound, ctx.config.STOP),
        )
    )


def sound_test(ctx):
    ctx.api.execute(ctx.model.Configs(ctx.model.Config(ctx.Sound, f"{request.host_url}static/alert.mp3")))


def _daytime(ctx):
    return 10 <= ctx.api.local_now().hour < 22


def trigger_sensor(ctx, device_id, event):
    if int(device_id) != _SENSOR_ID_ENTRANCE:
        return

    entrance = (ctx.Light.ENTRANCE_BULB_1, ctx.Light.ENTRANCE_BULB_2)

    if event == _SENSOR_EVENT_ACTIVE:
        ctx.api.log(ctx.api.local_now(), ctx.model.LogSource.SYSTEM, Log.ENTRANCE_SENSOR_TRIGGERED)
        ctx.api.execute(ctx.model.Config(entrance, 20))
        if _daytime(ctx):
            ctx.api.execute(ctx.config.default_config)
    else:
        ctx.api.execute(ctx.model.Config(entrance, ctx.config.OFF))

    ctx.scheduler.add_job(
        _run_trigger_sensor_off,
        DateTrigger(ctx.api.local_now() + timedelta(minutes=5)),
        name="Trigger Sensor",
        id="trigger-sensor",
        replace_existing=True,
        jobstore=JOBSTORE_MEMORY,
    )


@requires_ctx
def _run_trigger_sensor_off(ctx):
    plugin_ctx = build_ctx(ctx.config_manager, ctx.scheduler)
    log = lambda msg: plugin_ctx.api.log(
        plugin_ctx.api.local_now(), plugin_ctx.model.LogSource.SYSTEM, Log.TRIGGER_SENSOR_OFF_PREFIX.format(msg=msg)
    )

    for name in list(plugin_ctx.config_manager.presence()):
        plugin_ctx.api.expire_presence(plugin_ctx.config_manager, name)
    plugin_ctx.api.check_presence(ctx=ctx)

    if not _daytime(plugin_ctx):
        log(Log.TRIGGER_SENSOR_OFF_SKIPPED_NIGHTTIME)
        return
    if plugin_ctx.config_manager.present_names:
        log(Log.TRIGGER_SENSOR_OFF_SKIPPED_PRESENT.format(names=", ".join(sorted(plugin_ctx.config_manager.present_names))))
        return
    if sum(1 for s in plugin_ctx.api.capture_sounds().items if s.content) >= 2:
        log(Log.TRIGGER_SENSOR_OFF_SKIPPED_SOUNDS)
        return

    log(Log.TRIGGER_SENSOR_OFF_APPLIED)
    plugin_ctx.api.execute(plugin_ctx.model.squish_configs(plugin_ctx.config.default_config, state_override=plugin_ctx.config.OFF))
