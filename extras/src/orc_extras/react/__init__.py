from collections.abc import Iterable
from datetime import timedelta
from functools import partial
from pathlib import Path
from typing import Any

from command_cfg import each

import orc_extras.react
from orc.kernel import cast, engine
from orc.kernel.loader import load_plugin_config, validate_ac_state
from orc.model import (
    AcChannel,
    AcCommand,
    AcState,
    AnyoneChannel,
    AppContext,
    DeviceEnum,
    Devices,
    MqttDeviceChannel,
    Playback,
    Tag,
)
from orc_extras.react import model, plugins
from orc_extras.react.model import TRIGGERS, When
from orc_extras.react.web import react_bp

CONFIG = "orc_extras/react"
_OPTIONS = "[if <device> is <condition>] [--delay=<minutes>] [--pause=<minutes>]"
GRAMMAR = f"""
react <name> <devices> turns <state> set <action> {_OPTIONS}
react <name> <devices> turns <state> set <target> <action> {_OPTIONS}
react <name> <devices> <expr> between <low> and <high> set <target> <action> {_OPTIONS}
react <name> <devices> <expr> between <low> and <high> present <people> set <target> <action> {_OPTIONS}
"""


_AC_CONDITIONS = {name.lower(): state for name, state in AcState.__members__.items()}
_CHROMECAST_CONDITIONS = {state.value: state for state in Playback}


def _is_type(device: DeviceEnum, objects: dict[str, Any], type_name: str) -> bool:
    cls = objects["device"].enums.get(type_name)
    return bool(cls and isinstance(device, cls))


def _parse_target(target: str | None, action: Any, objects: dict[str, Any]) -> Devices | None:
    if target is None:
        if not isinstance(action, AcCommand):
            return None
        elif (ac_cls := objects["device"].enums.get("AC")) is None:  # a defined-but-empty AC enum is falsy yet still a valid target
            raise ValueError(f"AC command {action} requires an AC device type")
        return Devices(ac_cls)
    devices = cast.devices(target, objects)
    validate_ac_state(devices.all(), action, objects["device"].enums, source=target)
    return devices


def _parse_when(device: DeviceEnum, condition: str, objects: dict[str, Any]) -> When:
    if _is_type(device, objects, "AC"):
        state = _AC_CONDITIONS.get(condition)
        if state is None:
            raise ValueError(f"Invalid if condition {condition!r}: expected one of {sorted(_AC_CONDITIONS)}")
        return When(device, state)
    elif _is_type(device, objects, "Chromecast"):
        playback = _CHROMECAST_CONDITIONS.get(condition)
        if playback is None:
            raise ValueError(f"Invalid if condition {condition!r}: expected one of {sorted(_CHROMECAST_CONDITIONS)}")
        return When(device, playback)
    elif condition not in TRIGGERS:
        raise ValueError(f"Invalid if condition {condition!r}: expected one of {sorted(TRIGGERS)}")
    return When(device, condition)


def _group(reactions: Iterable[model.Reaction]) -> dict[str, model.Group]:
    groups: dict[str, model.Group] = {}
    for reaction in reactions:
        found = groups.get(reaction.name)
        if found is None:
            groups[reaction.name] = model.Group((reaction.automation.rule,), reaction.pause)
        elif found.pause != reaction.pause:
            raise ValueError(f"react {reaction.name!r}: every line sharing a name needs the same --pause")
        else:
            groups[reaction.name] = found._replace(rules=(*found.rules, reaction.automation.rule))
    return groups


def _pause(minutes: int | None) -> timedelta:
    if minutes is None:
        return model.DEFAULT_PAUSE
    elif minutes <= 0:
        raise ValueError(f"Invalid --pause {minutes!r}: expected a positive number of minutes")
    return timedelta(minutes=minutes)


def _range_rule(objects: dict[str, Any], args: Any) -> None:
    action = cast.state(args.action)
    target = _parse_target(args.target, action, objects)
    assert target is not None
    when = _parse_when(cast.device(args.device, objects), args.condition, objects) if args.device else None
    delay = timedelta(minutes=args.delay) if args.delay else timedelta()
    pause = _pause(args.pause)
    for source in cast.devices(args.devices, objects).all():
        formula = model.FormulaChannel(source, args.expr)
        conditions: list[engine.Condition] = []
        if args.people == Tag.ANYONE:
            conditions.append(engine.Is(AnyoneChannel(), True))
        elif args.people:
            people = tuple(name.strip() for name in args.people.split(","))
            conditions.append(model.Present(people))
        conditions.extend(model.condition(when))
        if isinstance(action, AcCommand):
            conditions.extend(model.AcIs(AcChannel(ac), AcState.OFF) for ac in target.all())
        conditions.append(model.Range(formula, args.low, args.high))
        command = engine.Command(target, action)
        rule = engine.Rule((engine.Clause(tuple(conditions), command),))
        automation = engine.Automation(model.DeviceChanged(source, args.expr), rule, delay, model.COOLDOWN)
        objects["react"].append(model.Reaction(automation, pause, args.name))


def _rule(objects: dict[str, Any], args: Any) -> None:
    if args.low is not None:
        _range_rule(objects, args)
        return
    attribute = TRIGGERS.get(args.state)
    if attribute is None:
        raise ValueError(f"Invalid trigger state {args.state!r}: expected one of {sorted(TRIGGERS)}")
    action = cast.state(args.action)
    target = _parse_target(args.target, action, objects)
    when = _parse_when(cast.device(args.device, objects), args.condition, objects) if args.device else None
    cond = model.condition(when)
    delay = timedelta(minutes=args.delay) if args.delay else timedelta()
    pause = _pause(args.pause)
    for source in cast.devices(args.devices, objects).all():
        command = engine.Command(target or Devices(source), action)
        channel = MqttDeviceChannel(source, attribute)
        rule = engine.Rule((engine.Clause(cond, command),))
        automation = engine.Automation(
            model.Transition(channel, args.state), rule, delay, model.COOLDOWN, cancel=engine.Has(model.ChangeChannel(channel))
        )
        objects["react"].append(model.Reaction(automation, pause, args.name))


def declare(declarations: Any) -> None:
    declarations.declare(setup=[setup], blueprints={"rules": react_bp}, scripts=[Path(__file__).parent / "static" / "react.js"])


def setup(ctx: AppContext) -> tuple[engine.Automation[Devices], ...]:
    cfg = load_plugin_config(
        CONFIG,
        ctx.config,
        GRAMMAR,
        serializers={"react": each(_rule, default=list, types={"delay": int, "low": int, "high": int, "pause": int})},
    )
    automations = tuple(reaction.automation for reaction in cfg.react)
    groups = _group(cfg.react)
    ctx.plugin_state[orc_extras.react] = model.State(
        automations, groups, {rule: name for name, group in groups.items() for rule in group.rules}
    )
    sources = {model.source_of(automation).value: model.source_of(automation) for automation in automations}
    ctx.api.add_listener(partial(plugins._on_event, ctx, sources))
    return automations
