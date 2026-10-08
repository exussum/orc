from collections.abc import Callable, Iterable, Mapping
from datetime import timedelta
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from command_cfg import each
from orc_engine import model as em

import orc_extras.react
from orc.kernel import cast
from orc.kernel.loader import ac_state, load_plugin_config
from orc.model import (
    OFF,
    ON,
    AcMode,
    AcState,
    AnyoneSubject,
    AppContext,
    DeviceEnum,
    Devices,
    OutsideTemperatureSubject,
)
from orc_extras.react import model, plugins
from orc_extras.react.model import (
    FUNCTIONS,
    TRIGGERS,
    AcIs,
    AcSubject,
    CastSubject,
    Device,
    FormulaSubject,
    MqttDeviceSubject,
    Present,
    Range,
    Transition,
)
from orc_extras.react.web import react_bp

CONFIG = "orc_extras/react"
_OPTIONS = "[--delay=<minutes>] [--pause=<minutes>]"
GRAMMAR = f"""
react <name> <devices> turns <to> [if <condition>...] set <action> {_OPTIONS}
react <name> <devices> turns <to> [if <condition>...] set <target> <action> {_OPTIONS}
react <name> <devices> if <condition>... set <action> {_OPTIONS}
react <name> <devices> if <condition>... set <target> <action> {_OPTIONS}
"""
_ERR_BUILD = "react condition {!r}: {}"
_ERR_CONDITION = "react condition {!r} is not a condition"
_ERR_STATE = "react condition {!r}: unknown state {!r}, expected one of {}"
_AC_STATES = (ON, OFF, *(mode.value for mode in AcMode))
_WHOLE_DEVICE: Mapping[str, Any] = {"Chromecast": CastSubject, "AC": AcSubject}


class _Device:
    """`Light.desk` — attribute access off it names one of that device's MQTT attributes."""

    def __init__(self, device: DeviceEnum) -> None:
        self.device = device

    def __getattr__(self, attribute: str) -> MqttDeviceSubject:
        return MqttDeviceSubject(self.device, attribute)


class _Type:
    """`Light` — a device type, indexed by member name to reach a `_Device`."""

    def __init__(self, name: str, enum: type[DeviceEnum]) -> None:
        self.name, self.enum = name, enum

    def __getattr__(self, member: str) -> Any:
        if (subject := _WHOLE_DEVICE.get(self.name)) is not None:
            return subject(self.enum[member])
        return _Device(self.enum[member])


class _Names(dict[str, Any]):
    """Bare names fall through to the source device, so `switch` means this rule's own switch."""

    def __init__(self, source: DeviceEnum, bound: Mapping[str, Any]) -> None:
        super().__init__(bound)
        self._source = source

    def __missing__(self, name: str) -> Any:
        if name in FUNCTIONS:
            return _formula(self._source, name)
        return MqttDeviceSubject(self._source, name)


def declare(declarations: Any) -> None:
    declarations.declare(setup=[setup], blueprints={"rules": react_bp}, scripts=[Path(__file__).parent / "static" / "react.js"])


def setup(ctx: AppContext) -> tuple[em.Watch[Devices], ...]:
    cfg = load_plugin_config(
        CONFIG,
        ctx.config,
        GRAMMAR,
        serializers={"react": each(_rule, default=list, types={"delay": int, "pause": int})},
    )
    watches = tuple(reaction.watch for reaction in cfg.react)
    groups = _group(cfg.react)
    ctx.plugin_state[orc_extras.react] = model.State(
        watches, groups, {watch: name for name, group in groups.items() for watch in group.watches}
    )
    sources = {reaction.source.value: reaction.source for reaction in cfg.react}
    ctx.api.add_listener(partial(plugins._on_event, ctx, sources))
    return watches


def namespace(source: DeviceEnum, types: Mapping[str, type[DeviceEnum]]) -> _Names:
    bound: dict[str, Any] = dict(VOCABULARY)
    bound.update({name: _Type(name, enum) for name, enum in types.items()})
    return _Names(source, bound)


def _group(reactions: Iterable[model.Reaction]) -> dict[str, model.Group]:
    groups: dict[str, model.Group] = {}
    for reaction in reactions:
        found = groups.get(reaction.name)
        if found is None:
            groups[reaction.name] = model.Group((reaction.watch,), reaction.pause)
        elif found.pause != reaction.pause:
            raise ValueError(f"react {reaction.name!r}: every line sharing a name needs the same --pause")
        else:
            groups[reaction.name] = found._replace(watches=(*found.watches, reaction.watch))
    return groups


def _pause(minutes: int | None) -> timedelta:
    if minutes is None:
        return model.DEFAULT_PAUSE
    elif minutes <= 0:
        raise ValueError(f"Invalid --pause {minutes!r}: expected a positive number of minutes")
    return timedelta(minutes=minutes)


def _rule(objects: dict[str, Any], args: Any) -> None:
    target, action = _parse_target(args.target, cast.state(args.action), objects)
    types = objects["device"].enums
    delay = timedelta(minutes=args.delay) if args.delay else timedelta()
    pause = _pause(args.pause)
    condition = " ".join(args.condition) if args.condition else None
    for source in cast.devices(args.devices, objects).all():
        guards = [_build(source, condition, types)] if condition else []
        if args.to is None:
            # No `turns`: the rule watches its device for any report.
            trigger: em.Condition = em.Changed(Device(source))
            cancel: em.Condition = em.NEVER
        else:
            # A pending command is dropped the moment that attribute changes again, so a light
            # switched off by hand is not switched off again when the delay elapses.
            subject = MqttDeviceSubject(source, _attribute(args.to))
            trigger = model.Transition(subject, args.to)
            cancel = em.Changed(subject)
        command = em.Command(target or Devices(source), action)
        rule = em.Rule((em.Step(em.And(*guards), command),))
        watch = em.Watch(trigger, rule, delay, model.COOLDOWN, cancel)
        objects["react"].append(model.Reaction(watch, pause, args.name, source))


def _parse_target(target: str | None, action: Any, objects: dict[str, Any]) -> tuple[Devices | None, Any]:
    if target is None:
        if not isinstance(action, AcState):
            return None, action
        elif (ac_cls := objects["device"].enums.get("AC")) is None:  # a defined-but-empty AC enum is falsy yet still a valid target
            raise ValueError(f"AC command {action} requires an AC device type")
        return Devices(ac_cls), action
    devices = cast.devices(target, objects)
    return devices, ac_state(devices.all(), action, objects["device"].enums, source=target)


def _attribute(to: str) -> str:
    if (attribute := TRIGGERS.get(to)) is None:
        raise ValueError(f"Invalid trigger state {to!r}: expected one of {sorted(TRIGGERS)}")
    return attribute


def _build(source: DeviceEnum, text: str, types: Mapping[str, type[DeviceEnum]]) -> em.Condition:
    """Build the condition `text` describes for a rule whose device is `source`."""
    try:
        built = eval(text, {"__builtins__": {}}, namespace(source, types))  # noqa: S307  # nosemgrep: python.lang.security.audit.eval-detected.eval-detected
    except Exception as exc:
        raise ValueError(_ERR_BUILD.format(text, exc)) from exc
    if not isinstance(built, em.Condition):
        raise ValueError(_ERR_CONDITION.format(text))
    return built


def _formula(source: DeviceEnum, name: str) -> Callable[..., FormulaSubject]:
    def make(*attributes: MqttDeviceSubject) -> FormulaSubject:
        return FormulaSubject(source, f"{name}({','.join(a.attribute for a in attributes)})")

    return make


def _ac_is(subject: AcSubject, state: str) -> AcIs:
    if state not in _AC_STATES:
        raise ValueError(_ERR_STATE.format("AcIs", state, sorted(_AC_STATES)))
    return AcIs(subject, state)


def _became(*args: Any) -> Transition:
    match args:
        case (subject, state):
            return Transition(subject, state)
        case (state,) if state in TRIGGERS:
            raise ValueError(f"Became({state!r}) needs the attribute too; write Became(<attribute>, {state!r})")
    raise ValueError(f"Became{args!r}: expected Became(<attribute>, <state>)")


VOCABULARY: Mapping[str, Any] = {
    "And": em.And,
    "Or": em.Or,
    "Eq": em.Eq,
    "Has": em.Has,
    "In": em.In,
    "Never": em.Never,
    "Became": _became,
    "Range": Range,
    "Entered": lambda subject, low, high: Range(subject, low, high, edge=True),
    "AcIs": _ac_is,
    "Present": lambda *names: Present(tuple(names)),
    "Anyone": lambda: em.Eq(AnyoneSubject(), True),
    "Nobody": lambda: em.Eq(AnyoneSubject(), False),
    "Playing": lambda device: em.Eq(device, "playing"),
    "Outside": SimpleNamespace(temperature=OutsideTemperatureSubject()),
}
