from datetime import time
from functools import partial
from typing import Any, NamedTuple

from command_cfg import group, scalar

from orc.kernel.loader import Cast, load_plugin_config, resolve_device
from orc.model import AppContext, DeviceEnum
from orc_extras.entrance_sensor import plugins

CONFIG = "orc_extras/entrance_sensor"
GRAMMAR = """
setting <key> <value>
message <log> <message>
rules <trigger> <devices> <state>
timed define <name> <start> <stop>
timed append <name> <devices> <state>
"""


def _devices(ctx: AppContext) -> dict[str, type]:
    return {name: dt.cls for name, dt in ctx.config.registry.devices.items()}


class Settings(NamedTuple):
    cleanup_delay_minutes: int
    entrance: DeviceEnum
    patio_door: DeviceEnum
    active_event: str
    inactive_event: str
    snapshot: int


class Messages(NamedTuple):
    log_present: str
    log_door_open: str
    log_absent: str
    log_shutdown: str


class Rule(NamedTuple):
    devices: Any
    state: Any


def _rule(ctx: AppContext, **values: Any) -> Rule:
    return Rule(devices=resolve_device(values["devices"], _devices(ctx)), state=Cast.state(values["state"]))


class Rules(NamedTuple):
    enter: list[Rule]
    inside: list[Rule]
    present: list[Rule]
    absent: list[Rule]
    shutdown: list[Rule]


class Timed(NamedTuple):
    start: time
    stop: time
    devices: Any
    state: Any


def _timed(ctx: AppContext, **values: Any) -> Timed:
    return Timed(
        start=Cast.clock(values["start"]),
        stop=Cast.clock(values["stop"]),
        devices=resolve_device(values["devices"], _devices(ctx)),
        state=Cast.state(values["state"]),
    )


def declare(declarations: Any) -> None:
    declarations.declare(setup=[setup])


def setup(ctx: AppContext) -> None:
    sensor = load_plugin_config(
        CONFIG,
        ctx.config,
        GRAMMAR,
        serializers={
            "setting": scalar(
                Settings,
                types={"cleanup_delay_minutes": Cast.int, "entrance": Cast.device, "patio_door": Cast.device, "snapshot": Cast.int},
            ),
            "message": scalar(Messages),
            "rules": group(partial(_rule, ctx)),
            "timed": group(partial(_timed, ctx)),
        },
    )
    sensor.rules = Rules(**sensor.rules)
    names = {str(sensor.setting.entrance.value), str(sensor.setting.patio_door.value)}
    ctx.api.add_listener(partial(plugins._on_sensor_event, ctx, sensor, names))
    ctx.api.add_state_provider("Entrance Sensors", partial(plugins.battery_state, ctx, names))
