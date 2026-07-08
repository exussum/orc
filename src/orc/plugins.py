from __future__ import annotations

import os
import signal
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

from apscheduler.schedulers.base import BaseScheduler

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
    ctx.config.plugins[id].func(ctx)


def light_test(ctx):
    end = ctx.api.local_now() + timedelta(minutes=10)
    ctx.snapshot_manager.replace_config(ctx.model.Config(ctx.orc.Light, ctx.model.OFF), end)
    ctx.api.light_test()
    ctx.snapshot_manager.resume(ctx.config.default_config)


def pair_lg_tv(ctx):
    for tv in ctx.orc.LGTV:
        ctx.api.pair_lg_tv(tv)


def rebuild_jobs(ctx):
    ctx.scheduler.remove_all_jobs()
    ctx.api.setup_scheduler(ctx)


def reboot(ctx):
    os.kill(os.getppid(), signal.SIGTERM)


def reboot_hubitat(ctx):
    ctx.api.reboot_hubitat()


@plugin_config("test_yolink_sensor", schema={"Settings": ("Key", "Value")})
def test_yolink_sensor(ctx, cfg):
    ctx.api.test_yolink(cfg.sensor)


def sound_test(ctx):
    base = ctx.config.internal_url.rstrip("/") + "/"
    url = f"{base}static/alert.mp3"
    ctx.api.execute(ctx.model.Configs(ctx.model.Config(ctx.orc.Chromecast, url)))
    ctx.api.play_text("audio test")
    alert_path = str(Path(__file__).parent / "static" / "alert.wav")
    for level in (ctx.model.AUDIO_INFO, ctx.model.AUDIO_FATAL):
        ctx.api.play_alert(alert_path, level=level)
