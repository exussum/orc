from datetime import time
from functools import partial
from typing import Any, NamedTuple

from command_cfg import group, scalar

from orc.kernel.loader import Cast, load_plugin_config
from orc.model import AppContext, Commands, DeviceEnum
from orc_extras.entrance_sensor import plugins

# Presence is paused at the door event, so cleanup only counts tags heard after
# it — and a staying tag's advertisements can drop out for up to ~42s.
MIN_BLE_CLEANUP_MINUTES = 1
CONFIG = "orc_extras/entrance_sensor"
GRAMMAR = """
setting <key> <value>
message <log> <message>
rules <trigger> <routine>
timed <name> <start> <stop> <routine>
"""


class Settings(NamedTuple):
    cleanup_delay_minutes: int
    entrance: DeviceEnum
    patio_door: DeviceEnum
    active_event: str
    inactive_event: str
    snapshot: int
    listener: str


class Messages(NamedTuple):
    log_present: str
    log_door_open: str
    log_absent: str
    log_shutdown: str


class Rules(NamedTuple):
    inside: str
    present: str
    absent: str
    shutdown: str


class Timed(NamedTuple):
    start: time
    stop: time
    commands: Commands


def _routine_commands(ctx: AppContext, name: str) -> Commands:
    # An ad_hoc's delay/snapshot are UI affordances - the plugin runs the
    # commands immediately, composing the reset base exactly like api.run_action.
    if (ad_hoc := ctx.config.ad_hoc_routines.get(name)) is not None:
        base = ctx.config.reset_config.commands if ad_hoc.reset else ()
        return (*base, *ad_hoc.commands)
    if (routine := ctx.config.routines.get(name)) is not None:
        return routine.commands
    raise ValueError(f"unknown routine {name!r} — expected an ad_hoc name or routine id")


def _rule(ctx: AppContext, **values: Any) -> str:
    ctx.config.ad_hoc_routines[values["routine"]]
    return values["routine"]


def _timed(ctx: AppContext, **values: Any) -> Timed:
    return Timed(start=Cast.clock(values["start"]), stop=Cast.clock(values["stop"]), commands=_routine_commands(ctx, values["routine"]))


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
    sensor.rules = Rules(**{trigger: rows[0] for trigger, rows in sensor.rules.items()})
    if sensor.setting.listener not in ctx.config.people:
        raise ValueError(f"unknown listener {sensor.setting.listener!r} — expected one of {tuple(ctx.config.people)}")
    if ctx.config.ble_tags and sensor.setting.cleanup_delay_minutes < MIN_BLE_CLEANUP_MINUTES:
        raise ValueError(f"cleanup_delay_minutes {sensor.setting.cleanup_delay_minutes} — BLE tags need at least {MIN_BLE_CLEANUP_MINUTES}")
    ctx.api.add_listener(partial(plugins._on_sensor_event, ctx, sensor))
    ctx.api.add_state_provider("Entrance Sensors", partial(plugins.battery_state, ctx, sensor))
