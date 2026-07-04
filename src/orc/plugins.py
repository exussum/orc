from __future__ import annotations

import os
import signal
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

from apscheduler.schedulers.base import BaseScheduler
from apscheduler.triggers.date import DateTrigger
from flask import request

from orc._decorators import plugin_config, requires_ctx

if TYPE_CHECKING:
    from orc import Config as OrcConfig
    from orc.api import SnapshotManager


@dataclass
class PluginCtx:
    snapshot_manager: SnapshotManager
    config: OrcConfig
    api: ModuleType
    model: ModuleType
    orc: ModuleType
    scheduler: BaseScheduler | None = None


def all_lights_off(ctx):
    ctx.api.execute(
        ctx.model.Configs(
            ctx.model.Config(ctx.orc.Light, ctx.model.OFF),
        )
    )


def all_lights_on(ctx):
    ctx.api.execute(ctx.model.Configs(ctx.model.Config(ctx.orc.Light, ctx.model.ON), ctx.model.Config(ctx.orc.Light, 100)))


def back_on_schedule(ctx):
    ctx.api.replay_day(ctx.api.local_now())


def build_ctx(orc_ctx):
    import orc
    from orc import api, config, model

    return PluginCtx(
        snapshot_manager=orc_ctx.snapshot_manager,
        scheduler=orc_ctx.scheduler,
        config=config,
        api=api,
        model=model,
        orc=orc,
    )


def execute_plugin(orc_ctx, id):
    ctx = build_ctx(orc_ctx)
    ctx.config.plugins[id](ctx)


def light_test(ctx):
    end = ctx.api.local_now() + timedelta(minutes=10)
    ctx.snapshot_manager.replace_config(ctx.model.Config(ctx.orc.Light, ctx.model.OFF), end)
    ctx.api.light_test()
    ctx.snapshot_manager.resume(ctx.config.default_config)


def pair_lg_tv(ctx):
    for tv in ctx.orc.LGTV:
        ctx.api.pair_lg_tv(tv)


def reboot(ctx):
    os.kill(os.getppid(), signal.SIGTERM)


def reboot_hubitat(ctx):
    ctx.api.reboot_hubitat()


def silence(ctx):
    ctx.api.execute(
        ctx.model.Configs(
            ctx.model.Config(ctx.orc.Chromecast, ctx.model.STOP),
        )
    )


def sound_test(ctx):
    base = ctx.config.internal_url.rstrip("/") + "/" if ctx.config.internal_url else request.host_url
    url = f"{base}static/alert.mp3"
    ctx.api.execute(ctx.model.Configs(ctx.model.Config(ctx.orc.Chromecast, url)))
    ctx.api.play_text("audio test")
    alert_path = str(Path(__file__).parent / "static" / "alert.wav")
    for level in (ctx.model.AUDIO_INFO, ctx.model.AUDIO_FATAL):
        ctx.api.play_alert(alert_path, level=level)


@plugin_config(
    "sensor",
    schema={
        "Settings": ("Key", "Value"),
        "Messages": ("Log", "Message"),
        "Day": ("Trigger", "Device", "State"),
        "Night": ("Trigger", "Device", "State"),
    },
)
def trigger_sensor(ctx, sensor, device_id, event):
    if int(device_id) != sensor.entrance_id:
        return

    hour = ctx.api.local_now().hour
    daytime = sensor.day_start <= hour < sensor.day_end
    phase = sensor.day if daytime else sensor.night

    if event == sensor.active_event:
        ctx.api.execute(_to_configs(ctx, phase.entrance_light))
        ctx.api.execute(_to_configs(ctx, phase.entrance_config))
    elif event == sensor.inactive_event:
        ctx.api.execute(ctx.model.squish_configs(_to_configs(ctx, phase.entrance_light), state_override=ctx.model.OFF))
        ctx.scheduler.add_job(
            _run_trigger_sensor_off,
            DateTrigger(ctx.api.local_now() + timedelta(minutes=sensor.cleanup_delay_minutes), timezone=ctx.config.tz),
            name="Trigger Sensor",
            id="trigger-sensor",
            replace_existing=True,
            jobstore=ctx.api.JOBSTORE_MEMORY,
            args=(sensor,),
        )


@plugin_config("video_conference", schema={"Lights": ("Trigger", "Device", "State")})
def video_conference(ctx, vc):
    ctx.api.execute(_to_configs(ctx, vc.lights.lights))


@requires_ctx
def _run_trigger_sensor_off(sensor, *, ctx):
    plugin_ctx = build_ctx(ctx)
    hour = plugin_ctx.api.local_now().hour
    daytime = sensor.day_start <= hour < sensor.day_end
    phase = sensor.day if daytime else sensor.night

    plugin_ctx.api.expire_presence(list(plugin_ctx.api.last_seen()))
    present = plugin_ctx.api.check_presence(ctx=ctx)

    if not daytime:
        plugin_ctx.api.execute(_to_configs(plugin_ctx, phase.after_hours))
        msg = sensor.log_after_hours
    elif present:
        plugin_ctx.api.execute(_to_configs(plugin_ctx, phase.after_hours))
        msg = sensor.log_present
    elif any(s.content for s in plugin_ctx.api.capture_sounds().items):
        plugin_ctx.api.execute(_to_configs(plugin_ctx, phase.core_hours))
        msg = sensor.log_core_hours
    else:
        plugin_ctx.api.execute(_to_configs(plugin_ctx, phase.shutdown))
        plugin_ctx.api.execute(_to_configs(plugin_ctx, phase.core_hours))
        msg = sensor.log_shutdown
    plugin_ctx.api.log(plugin_ctx.api.local_now(), plugin_ctx.model.LogSource.SYSTEM, msg)


def _to_configs(ctx, rows):
    return ctx.model.Configs(*[ctx.model.Config(r.device, r.state) for r in rows])
