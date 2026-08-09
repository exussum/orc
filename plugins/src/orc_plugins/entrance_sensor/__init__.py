import sys
from datetime import time
from functools import partial
from typing import Any, NamedTuple

from orc_plugins.entrance_sensor import plugins

from orc.loader import load_plugin_config
from orc.model import AppContext

CONFIG = "orc_plugins/entrance_sensor"
GRAMMAR = """
setting <key> <value>
message <log> <message>
rules <trigger> <device> <state>
timed define <name> <start> <stop>
timed append <name> <device> <state>
"""


class Settings(NamedTuple):
    cleanup_delay_minutes: int
    entrance_id: int
    patio_door_id: int
    active_event: str
    inactive_event: str
    snapshot: int


class Messages(NamedTuple):
    log_present: str
    log_door_open: str
    log_absent: str
    log_shutdown: str


class Rule(NamedTuple):
    device: Any
    state: Any


class Rules(NamedTuple):
    enter: list[Rule]
    inside: list[Rule]
    present: list[Rule]
    absent: list[Rule]
    shutdown: list[Rule]


class Timed(NamedTuple):
    start: time
    stop: time
    device: Any
    state: Any


def declare(declarations: Any) -> None:
    declarations.declare(setup=[setup])


def setup(ctx: AppContext) -> None:
    try:
        sensor = load_plugin_config(
            CONFIG,
            ctx.config.plugin_configs,
            GRAMMAR,
            serializers={"setting": Settings, "message": Messages, "rules": Rule, "timed": Timed},
            scalars=("setting", "message"),
            grouped=("rules", "timed"),
        )
        sensor.rules = Rules(**sensor.rules)
    except Exception as exc:
        print(f"Failed to load plugin config {CONFIG!r}: {exc}", file=sys.stderr)
        return
    ids = {sensor.setting.entrance_id, sensor.setting.patio_door_id}
    ctx.api.add_listener(partial(plugins._on_sensor_event, ctx, sensor, ids))
    ctx.api.add_state_provider("Entrance Sensors", partial(plugins.battery_state, ctx, ids))
