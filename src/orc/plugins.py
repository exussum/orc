from __future__ import annotations

import os
import signal
import sys
from dataclasses import dataclass
from datetime import timedelta
from types import ModuleType
from typing import TYPE_CHECKING

from apscheduler.triggers.date import DateTrigger
from flask import request

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


def execute_plugin(config_manager, id):
    from orc import Light, Sound, api, config, model

    ctx = PluginCtx(
        config_manager=config_manager,
        Light=Light,
        Sound=Sound,
        config=config,
        api=api,
        model=model,
    )
    getattr(sys.modules[__name__], config.plugins[id])(ctx)


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
    ctx.api.execute(ctx.model.Configs(ctx.model.Config(ctx.Light, "on"), ctx.model.Config(ctx.Light, 100)))


def all_lights_off(ctx):
    ctx.api.execute(
        ctx.model.Configs(
            ctx.model.Config(ctx.Light, "off"),
        )
    )


def video_conference(ctx):
    ctx.api.execute(
        ctx.model.Configs(
            ctx.model.Config(ctx.Light.OFFICE_TABLE, 5),
            ctx.model.Config(ctx.Light.OFFICE_FLOOR, "on"),
            ctx.model.Config(ctx.Light.OFFICE_DESK, 50),
        )
    )


def silence(ctx):
    ctx.api.execute(
        ctx.model.Configs(
            ctx.model.Config(ctx.Sound, "stop"),
        )
    )


def sound_test(ctx):
    ctx.api.execute(ctx.model.Configs(ctx.model.Config(ctx.Sound, f"{request.host_url}static/alert.mp3")))


def trigger_sensor(scheduler):
    from orc import api

    scheduler.add_job(
        api.run_trigger_sensor,
        DateTrigger(api.local_now() + timedelta(minutes=1)),
        name="Trigger Sensor",
        id="trigger-sensor",
        replace_existing=True,
        jobstore="memory",
    )
