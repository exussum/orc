import sys
from datetime import time
from functools import partial
from typing import Any, NamedTuple

from command_cfg import group, scalar

from orc.loader import Cast, load_plugin_config, resolve_device
from orc.model import AppContext, resolve_state
from orc_extras.entrance_sensor import plugins

CONFIG = "orc_extras/entrance_sensor"
GRAMMAR = """
setting <key> <value>
message <log> <message>
rules <trigger> <device> <state>
timed define <name> <start> <stop>
timed append <name> <device> <state>
"""

_SETTING_TYPES = {"cleanup_delay_minutes": int, "entrance_id": int, "patio_door_id": int, "snapshot": int}


def _devices() -> dict[str, type]:
    import orc

    return {name: dt.cls for name, dt in orc.config.registry.devices.items()}


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


def _rule(**values: Any) -> Rule:
    return Rule(device=resolve_device(values["device"], _devices()), state=resolve_state(values["state"]))


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


def _timed(**values: Any) -> Timed:
    return Timed(
        start=Cast.clock(values["start"]),
        stop=Cast.clock(values["stop"]),
        device=resolve_device(values["device"], _devices()),
        state=resolve_state(values["state"]),
    )


def declare(declarations: Any) -> None:
    declarations.declare(setup=[setup])


def setup(ctx: AppContext) -> None:
    try:
        sensor = load_plugin_config(
            CONFIG,
            ctx.config.plugin_configs,
            GRAMMAR,
            serializers={
                "setting": scalar(Settings, types=_SETTING_TYPES),
                "message": scalar(Messages),
                "rules": group(_rule),
                "timed": group(_timed),
            },
        )
        sensor.rules = Rules(**sensor.rules)
    except Exception as exc:
        print(f"Failed to load plugin config {CONFIG!r}: {exc}", file=sys.stderr)
        return
    ids = {sensor.setting.entrance_id, sensor.setting.patio_door_id}
    ctx.api.add_listener(partial(plugins._on_sensor_event, ctx, sensor, ids))
    ctx.api.add_state_provider("Entrance Sensors", partial(plugins.battery_state, ctx, ids))
