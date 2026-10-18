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
    AcState,
    AnyoneSubject,
    AppContext,
    DeviceEnum,
    Devices,
    OutsideTemperatureSubject,
)
from orc_extras.react import model, plugins
from orc_extras.react.model import (
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
phrase <name> <condition>...
react <name> <devices> turns <to> [if <condition>...] set <action> {_OPTIONS}
react <name> <devices> turns <to> [if <condition>...] set <target> <action> {_OPTIONS}
react <name> <devices> if <condition>... set <action> {_OPTIONS}
react <name> <devices> if <condition>... set <target> <action> {_OPTIONS}
"""
_ERR_BUILD = "react condition {!r}: {}"
_ERR_CONDITION = "react condition {!r} is not a condition"
_WHOLE_DEVICE: Mapping[str, Any] = {"Chromecast": CastSubject, "AC": AcSubject}


def declare(declarations: Any) -> None:
    declarations.declare(setup=[setup], blueprints={"rules": react_bp}, scripts=[Path(__file__).parent / "static" / "react.js"])


def setup(ctx: AppContext) -> tuple[em.Watch[Devices], ...]:
    cfg = load_plugin_config(
        CONFIG,
        ctx.config,
        GRAMMAR,
        serializers={
            "phrase": each(_phrase, default=dict),
            "react": each(_rule, default=list, types={"delay": int, "pause": int}),
        },
    )
    watches = tuple(reaction.watch for reaction in cfg.react)
    groups = _group(cfg.react)
    ctx.plugin_state[orc_extras.react] = model.State(
        watches, groups, {watch: name for name, group in groups.items() for watch in group.watches}
    )
    sources = {reaction.source.value: reaction.source for reaction in cfg.react}
    ctx.api.add_listener(partial(plugins._on_event, ctx, sources))
    return watches


def build_namespace(source: DeviceEnum, types: Mapping[str, type[DeviceEnum]]) -> _Namespace:
    bound: dict[str, Any] = dict(VOCABULARY)
    bound.update({name: _Type(name, enum) for name, enum in types.items()})
    bound["self"] = Devices(source)
    bound.update({name: _call(function) for name, function in FUNCTIONS.items()})
    return _Namespace(source, bound)


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


class _Namespace(dict[str, Any]):
    """Bare names fall through to the source device, so `switch` means this rule's own switch."""

    def __init__(self, source: DeviceEnum, bound: Mapping[str, Any]) -> None:
        super().__init__(bound)
        self._source = source

    def __missing__(self, name: str) -> Any:
        return MqttDeviceSubject(self._source, name)


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


def _phrase(objects: dict[str, Any], args: Any) -> None:
    if args.name in objects["phrase"]:
        raise ValueError(f"react phrase {args.name!r}: that name is already taken")
    objects["phrase"][args.name] = " ".join(args.condition)


def _rule(objects: dict[str, Any], args: Any) -> None:
    target, action = _parse_target(args.target, cast.state(args.action), objects)
    types = objects["device"].enums
    delay = timedelta(minutes=args.delay) if args.delay else timedelta()
    pause = _pause(args.pause)
    condition = " ".join(args.condition) if args.condition else None
    for source in cast.devices(args.devices, objects).all():
        guards = [_build(source, condition, types, objects["phrase"])] if condition else []
        if args.to is None:
            # No `turns`: the rule watches its device for any report.
            trigger: em.Condition = em.Changed(Device(source))
            cancel: em.Condition = em.NEVER
        else:
            # A pending command is dropped the moment that attribute changes again, so a light
            # switched off by hand is not switched off again when the delay elapses.
            if isinstance(source, types.get("AC", ())):
                subject, to = MqttDeviceSubject(source, "state"), _ac_state(args.to)
            else:
                subject, to = MqttDeviceSubject(source, _attribute(args.to)), args.to
            trigger = model.Transition(subject, to)
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


def _ac_state(value: str) -> AcState:
    state = AcState(power=value) if value in (ON, OFF) else cast.state(value)
    if not isinstance(state, AcState):
        raise ValueError(f"Invalid AC state {value!r}: expected on, off, or mode[:fan[:temp]]")
    return state


def _build(source: DeviceEnum, text: str, types: Mapping[str, type[DeviceEnum]], phrases: Mapping[str, str]) -> em.Condition:
    """Build the condition `text` describes for a rule whose device is `source`."""
    namespace = build_namespace(source, types)
    namespace["Phrase"] = built = SimpleNamespace()
    for name, phrase in phrases.items():
        setattr(built, name, _eval(phrase, namespace))
    return _eval(text, namespace)


def _eval(text: str, namespace: _Namespace) -> em.Condition:
    try:
        built = eval(text, {"__builtins__": {}}, namespace)  # noqa: S307  # nosemgrep: python.lang.security.audit.eval-detected.eval-detected
    except Exception as exc:
        raise ValueError(_ERR_BUILD.format(text, exc)) from exc
    if not isinstance(built, em.Condition):
        raise ValueError(_ERR_CONDITION.format(text))
    return built


def _ac_is(subject: AcSubject, state: str) -> AcIs:
    return AcIs(subject, _ac_state(state))


def _became(*args: Any) -> Transition:
    match args:
        case (subject, state):
            return Transition(subject, state)
        case (state,) if state in TRIGGERS:
            raise ValueError(f"Became({state!r}) needs the attribute too; write Became(<attribute>, {state!r})")
    raise ValueError(f"Became{args!r}: expected Became(<attribute>, <state>)")


def _call(function: Callable[..., float | None]) -> Callable[..., FormulaSubject]:
    def make(*args: Any) -> FormulaSubject:
        return FormulaSubject(function, tuple(_resolve(arg) for arg in args))

    return make


def _resolve(arg: Any) -> Any:
    if isinstance(arg, _Device):
        return Devices(arg.device)
    elif isinstance(arg, _Type):
        return Devices(arg.enum)
    elif isinstance(arg, MqttDeviceSubject):
        raise ValueError(f"{arg.attribute!r} is an attribute, not a device; name the device or use self")
    return arg


def _dewpoint(ctx: AppContext, devices: Devices, temperature: str, humidity: str) -> float | None:
    values = []
    for device in devices.all():
        found = ctx.api.device_state(str(device.value))
        values.append(ctx.api.dewpoint(found.attributes[temperature], found.attributes[humidity]))
    return sum(values) / len(values) if values else None


FUNCTIONS: Mapping[str, Callable[..., float | None]] = {"dewpoint": _dewpoint}

VOCABULARY: Mapping[str, Any] = {
    "And": em.And,
    "Or": em.Or,
    "Not": em.Not,
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
