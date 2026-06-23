from __future__ import annotations

import os
import signal
import sys
from concurrent.futures import ThreadPoolExecutor as Pool
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

from apscheduler.schedulers.base import BaseScheduler
from apscheduler.triggers.date import DateTrigger
from flask import request

from orc._decorators import requires_ctx
from orc.locale import Log

_SENSOR_ID_ENTRANCE = 16
_SENSOR_EVENT_ACTIVE = "active"

if TYPE_CHECKING:
    from orc import Config as OrcConfig
    from orc.api import SnapshotManager
    from orc.model import DeviceEnum


@dataclass
class PluginCtx:
    snapshot_manager: SnapshotManager
    Light: type[DeviceEnum]
    Chromecast: type[DeviceEnum]
    TV: type[DeviceEnum]
    config: OrcConfig
    api: ModuleType
    model: ModuleType
    scheduler: BaseScheduler | None = None


def all_lights_off(ctx):
    ctx.api.execute(
        ctx.model.Configs(
            ctx.model.Config(ctx.Light, ctx.config.OFF),
        )
    )


def all_lights_on(ctx):
    ctx.api.execute(ctx.model.Configs(ctx.model.Config(ctx.Light, ctx.config.ON), ctx.model.Config(ctx.Light, 100)))


def back_on_schedule(ctx):
    ctx.api.replay_day(ctx.api.local_now())


def build_ctx(snapshot_manager, scheduler=None):
    from orc import TV, Chromecast, Light, api, config, model

    return PluginCtx(
        snapshot_manager=snapshot_manager,
        Light=Light,
        Chromecast=Chromecast,
        TV=TV,
        config=config,
        api=api,
        model=model,
        scheduler=scheduler,
    )


def execute_plugin(snapshot_manager, id):
    ctx = build_ctx(snapshot_manager)
    getattr(sys.modules[__name__], ctx.config.plugins[id])(ctx)


def light_test(ctx):
    end = ctx.api.local_now() + timedelta(minutes=10)
    ctx.snapshot_manager.replace_config(ctx.model.Config(ctx.Light, ctx.config.OFF), end)
    ctx.api.light_test()
    ctx.snapshot_manager.resume(ctx.config.default_config)


def pair_lg_tv(ctx):
    for tv in ctx.TV:
        ctx.api.pair_lg_tv(tv.value)


def reboot(ctx):
    os.kill(os.getppid(), signal.SIGTERM)


def reboot_hubitat(ctx):
    ctx.api.reboot_hubitat()


def silence(ctx):
    ctx.api.execute(
        ctx.model.Configs(
            ctx.model.Config(ctx.Chromecast, ctx.config.STOP),
        )
    )


def sound_test(ctx):
    base = ctx.config.internal_url.rstrip("/") + "/" if ctx.config.internal_url else request.host_url
    url = f"{base}static/alert.mp3"
    ctx.api.execute(ctx.model.Configs(ctx.model.Config(ctx.Chromecast, url)))
    ctx.api.play_text("audio test")
    alert_path = str(Path(__file__).parent / "static" / "alert.wav")
    for level in (ctx.config.AUDIO_INFO, ctx.config.AUDIO_FATAL):
        ctx.api.play_alert(alert_path, level=level)


def trigger_sensor(ctx, device_id, event):
    if int(device_id) != _SENSOR_ID_ENTRANCE:
        return

    entrance = (ctx.Light.ENTRANCE_BULB_1, ctx.Light.ENTRANCE_BULB_2)

    if event == _SENSOR_EVENT_ACTIVE:
        ctx.api.execute(ctx.model.Config(entrance, 20))
        if _daytime(ctx):
            ctx.api.execute(ctx.config.default_config)
        _each_sound(ctx, ctx.api.pause)
    else:
        ctx.api.execute(ctx.model.Config(entrance, ctx.config.OFF))
        ctx.scheduler.add_job(
            _run_trigger_sensor_off,
            DateTrigger(ctx.api.local_now() + timedelta(minutes=2), timezone=ctx.config.tz),
            name="Trigger Sensor",
            id="trigger-sensor",
            replace_existing=True,
            jobstore=ctx.api.JOBSTORE_MEMORY,
        )


def video_conference(ctx):
    ctx.api.execute(
        ctx.model.Configs(
            ctx.model.Config(ctx.Light.OFFICE_TABLE, 5),
            ctx.model.Config(ctx.Light.OFFICE_FLOOR, ctx.config.ON),
            ctx.model.Config(ctx.Light.OFFICE_DESK, 50),
        )
    )


def _daytime(ctx):
    return 10 <= ctx.api.local_now().hour < 22


def _each_sound(ctx, action):
    with Pool(max_workers=len(ctx.Chromecast)) as ex:
        list(ex.map(action, ctx.Chromecast))


@requires_ctx
def _run_trigger_sensor_off(ctx):
    plugin_ctx = build_ctx(ctx.snapshot_manager, ctx.scheduler)
    log = lambda msg: plugin_ctx.api.log(
        plugin_ctx.api.local_now(), plugin_ctx.model.LogSource.SYSTEM, Log.TRIGGER_SENSOR_OFF_PREFIX.format(msg=msg)
    )

    plugin_ctx.api.expire_presence(list(plugin_ctx.api.last_seen()))
    present = plugin_ctx.api.check_presence(ctx=ctx)

    if not present:
        plugin_ctx.api.execute(plugin_ctx.model.Config(plugin_ctx.TV, plugin_ctx.config.OFF))

    if not _daytime(plugin_ctx):
        # If it's night, the lights were unaffected, just stop the sound.
        action, msg = plugin_ctx.api.stop, Log.TRIGGER_SENSOR_OFF_SKIPPED_NIGHTTIME
    elif present:
        # If people are around, they're in control of the lights, stop the sound.
        action, msg = plugin_ctx.api.stop, Log.TRIGGER_SENSOR_OFF_SKIPPED_PRESENT.format(names=", ".join(sorted(present)))
    elif any(s.content for s in plugin_ctx.api.capture_sounds().items):
        # Stuff is playing for a reason, resume it.
        action, msg = plugin_ctx.api.resume, Log.TRIGGER_SENSOR_OFF_SKIPPED_SOUNDS
    else:
        # If it's daytime, no one is home, and it's quiet, turn off the lights, resume the sound (in case it gets falsely paused)
        plugin_ctx.api.execute(plugin_ctx.model.squish_configs(plugin_ctx.config.default_config, state_override=plugin_ctx.config.OFF))
        action, msg = plugin_ctx.api.resume, Log.TRIGGER_SENSOR_OFF_APPLIED

    log(msg)
    _each_sound(plugin_ctx, action)
