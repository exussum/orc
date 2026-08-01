import os
import signal
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

from orc import model as m
from orc.decorators import plugin_config, requires_ctx  # noqa: F401

if TYPE_CHECKING:
    from orc import Config as OrcConfig


@dataclass
class PluginCtx(m.AppContext):
    config: OrcConfig
    api: ModuleType
    model: ModuleType
    orc: ModuleType


def back_on_schedule(ctx: PluginCtx) -> None:
    ctx.api.check_presence(silent=True)
    ctx.api.replay_day(ctx.api.local_now())


def build_ctx(orc_ctx: m.AppContext) -> PluginCtx:
    import orc
    from orc import api, config, model

    return PluginCtx(
        snapshot_manager=orc_ctx.snapshot_manager,
        scheduler=orc_ctx.scheduler,
        sound_path=orc_ctx.sound_path,
        version_manager=orc_ctx.version_manager,
        config=config,
        api=api,
        model=model,
        orc=orc,
    )


def execute_plugin(orc_ctx: m.AppContext, id: str, **params: Any) -> None:
    ctx = build_ctx(orc_ctx)
    ctx.config.plugins[id].func(ctx, **params)


def light_test(ctx: PluginCtx) -> None:
    end = ctx.api.local_now() + timedelta(minutes=10)
    ctx.snapshot_manager.replace_config("light_test", ctx.model.Config(ctx.orc.Light, ctx.model.OFF), end)
    ctx.api.light_test()
    ctx.snapshot_manager.resume("light_test", ctx.config.default_config)


def rebuild_jobs(ctx: PluginCtx) -> None:
    ctx.api.rebuild_jobs(ctx)


def reboot(ctx: PluginCtx) -> None:
    os.kill(os.getppid(), signal.SIGTERM)


def reboot_hubitat(ctx: PluginCtx) -> None:
    ctx.api.reboot_hubitat()


def sound_test(ctx: PluginCtx) -> None:
    base = ctx.config.internal_url.rstrip("/") + "/"
    url = f"{base}static/alert.mp3"
    ctx.api.dispatch(ctx.model.Configs(ctx.model.Config(ctx.orc.Chromecast, url)), force=True)
    ctx.api.play_text("audio test")
    alert_path = str(Path(__file__).parent / "static" / "alert.wav")
    for level in (ctx.model.AUDIO_INFO, ctx.model.AUDIO_FATAL):
        ctx.api.play_alert(alert_path, level=level)
