from functools import partial
from typing import Any, NamedTuple

from command_cfg import scalar

from orc.loader import Cast, load_plugin_config
from orc.model import AppContext, Devices
from orc_extras.max_on import plugins

CONFIG = "orc_extras/max_on"
GRAMMAR = """
setting <key> <value>
"""


class Settings(NamedTuple):
    devices: Devices
    minutes: int = 15


def declare(declarations: Any) -> None:
    declarations.declare(setup=[setup])


def setup(ctx: AppContext) -> None:
    cfg = load_plugin_config(
        CONFIG,
        ctx.config,
        GRAMMAR,
        serializers={"setting": scalar(Settings, types={"devices": Cast.devices, "minutes": Cast.int})},
    )
    by_id = {d.value: d for d in cfg.setting.devices.all()}
    ctx.api.add_listener(partial(plugins._on_switch_event, ctx, cfg.setting.minutes, by_id))
