import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, replace
from datetime import datetime, timedelta, tzinfo
from functools import partial
from types import ModuleType, SimpleNamespace
from typing import Any

import command_cfg
from command_cfg import ConfigError, array, each, group, raw, scalar
from orc_engine import cast as engine_cast
from orc_engine import model as em

from orc import model as m
from orc.collections import of_type
from orc.dal import interfaces
from orc.kernel import cast

_BUTTON_EVENTS = frozenset({"pushed", "held", "doubleTapped", "released"})

_POLAR_LATITUDE = 65.7

GRAMMAR = """
ad_hoc define <id> <name> [--snapshot=<minutes>] [--delay=<minutes>] [--section=<section>] [--no-reset] [<devices> <state>]
ad_hoc append <id> <devices> <state>

remote <device> <button> <event> <action>

device define <type> [--sort=<n>]
device add <type> <id> <target> [--room=<room>] [--name=<name>]
device only <type> [<id> <target>] [--room=<room>] [--name=<name>] [--sort=<n>]
device seal <type>

group <id> <title>

button <id> <group> [--stripe-from=<stripe_from> --stripe-to=<stripe_to>]

person <name> <host> <mac>

plugin <name> <module> [--section=<section>] [--icon=<icon>] [--backend=<module>]
plugin <name> <module> <function> --section=<section> [--icon=<icon>] [--backend=<module>]

provider <key> <module>

room <name> <devices> <state>

routine define <id> <name> [--skip-replay]
routine append <id> <devices> <state> [--trigger-person=<person>] [--trigger-weather=<weather>]

setting <key> <value>

tag <person> <secret> <pair_date>

theme <name> <routine> <time>
"""


def parse_config(text: str, zigbee_config: dict[Any, tuple[Any, ...]] | None = None) -> SimpleNamespace:
    serializers = {
        "person": group(m.Person),
        "device": each(
            partial(_device, zigbee_config or {}),
            default=lambda: SimpleNamespace(members={}, enums={}, sorts={}, virtual_devices=set()),
            types={"sort": int},
        ),
        "room": each(_room, default=dict),
        "ad_hoc": each(_ad_hoc, default=dict, types={"snapshot": int, "delay": int}),
        "remote": each(_remote, default=tuple, types={"button": int}),
        "routine": each(_routine, default=dict),
        "group": each(_group, default=tuple),
        "button": each(_button, default=tuple, types={"stripe_from": cast.when, "stripe_to": cast.when}),
        "theme": each(_theme, default=dict, types={"time": cast.when}),
        "plugin": each(_plugin, default=list, types={"module": engine_cast.module, "backend": engine_cast.module}),
        "provider": scalar(interfaces.Provider, types={field: engine_cast.module for field in interfaces.Provider._fields}),
        "setting": scalar(
            m.Settings.build,
            types={
                "lat": engine_cast.float,
                "long": engine_cast.float,
                "http_timeout": engine_cast.int,
                "port": engine_cast.int,
                "presence_hours": engine_cast.int,
                "checkin_hours": engine_cast.int,
                "sunset_lead_hours": engine_cast.int,
                "warning_device": cast.device,
                "attention_device": cast.device,
                "emergency_device": cast.device,
            },
        ),
        "tag": array(m.BleTag),
    }
    objects = command_cfg.load(text, GRAMMAR, serializers, variables=os.environ)
    if unsealed := objects["device"].members.keys() - objects["device"].enums.keys():
        raise ConfigError(f"Device types defined but never sealed: {sorted(unsealed)}")
    return SimpleNamespace(
        ad_hoc=objects["ad_hoc"],
        enums=objects["device"].enums,
        virtual_devices=objects["device"].virtual_devices,
        button=objects["button"],
        group=objects["group"],
        person=objects["person"],
        plugin_modules=[p.module for p in objects["plugin"]],
        plugins=tuple(p for p in objects["plugin"] if p.section is not None),
        provider=objects["provider"] or interfaces.Provider(),
        remote=objects["remote"],
        room=objects["room"],
        routine=objects["routine"],
        setting=objects["setting"] or m.Settings.build(),
        tag=objects["tag"],
        theme=objects["theme"],
    )


def validate(config: SimpleNamespace) -> None:
    for label, present, required in (
        ("routines", config.routine.keys(), ("ROUTINE_DEFAULT", "ROUTINE_RESET")),
        ("routine names", {r.name for r in config.routine.values()}, ("Reset",)),
        ("themes", config.theme.keys(), (m.THEME_WORK_DAY, m.THEME_DAY_OFF)),
    ):
        if missing := set(required) - present:
            raise ConfigError(f"Missing required {label}: {', '.join(sorted(missing))}")
    if unset := [key for key, value in zip(interfaces.Provider._fields, config.provider, strict=True) if value is None]:
        raise ConfigError(f"Missing required providers: {', '.join(unset)}")
    if unset := [key for key, value in zip(m.Settings._fields, config.setting, strict=True) if value in (None, "")]:
        raise ConfigError(f"Missing required settings: {', '.join(unset)}")
    if config.setting.emergency_routine not in config.routine:
        raise ConfigError(f"Unknown routine {config.setting.emergency_routine!r}: expected one of {tuple(config.routine)}")
    sun_timed = [e.routine.name for theme in config.theme.values() for e in theme.entries if e.when in (m.SUNRISE, m.SUNSET)]
    if sun_timed and abs(config.setting.lat) > _POLAR_LATITUDE:
        raise ConfigError(f"Latitude {config.setting.lat} has days without a sunrise or sunset: {', '.join(sun_timed)}")
    groups = {g.id for g in config.group}
    placeable = {id for id, routine in config.ad_hoc.items() if routine.section == "scene"}
    placeable |= {p.name for p in config.plugins if p.section == "scene"}
    for button in config.button:
        if button.id not in placeable:
            raise ConfigError(f"Unknown scene button {button.id!r}: expected one of {tuple(sorted(placeable))}")
        if button.group not in groups:
            raise ConfigError(f"Unknown group {button.group!r} for button {button.id!r}: expected one of {tuple(sorted(groups))}")
    for tag in config.tag:
        if tag.person not in config.person:
            raise ConfigError(f"Unknown person {tag.person!r} in tag line: expected one of {tuple(config.person)}")
        try:
            datetime.fromisoformat(tag.pair_date)
        except ValueError:
            raise ConfigError(f"Invalid pair_date {tag.pair_date!r} in tag line: expected ISO 8601") from None


def ble_keys(tags: list[m.BleTag], secrets: m.Secrets, tz: tzinfo) -> dict[str, m.BleKey]:
    """Per-person EID keys; empty when secrets aren't loaded (the blank first pass)."""
    if not secrets.other:
        return {}
    keys = {}
    for tag in tags:
        pair_date = datetime.fromisoformat(tag.pair_date)
        if not pair_date.tzinfo:
            pair_date = pair_date.replace(tzinfo=tz)
        keys[tag.person] = m.BleKey(_eik_reader(getattr(secrets, tag.secret)), int(pair_date.timestamp()))
    return keys


def _eik_reader(read: m.SecretReader) -> Callable[[], bytes]:
    return lambda: engine_cast.hex32(read())


def secret_needs(registry: m.Registry, providers: interfaces.Provider, tags: list[m.BleTag]) -> dict[str, Callable[[str], Any]]:
    needs: dict[str, Callable[[str], Any]] = {}
    for backend in providers:
        if backend:
            needs |= backend.REQUIRED_SECRETS
    needs |= registry.secrets
    needs |= {tag.secret: engine_cast.hex32 for tag in tags}
    return needs


def check_secrets(secrets: m.Secrets, needs: Mapping[str, Callable[[str], Any]]) -> dict[str, str]:
    values = asdict(secrets)
    values |= values.pop("other")
    problems = {}
    for name, shape in needs.items():
        value = values.get(name, "")
        if not value:
            problems[name] = "not set"
        else:
            try:
                shape(value)
            except ValueError:
                problems[name] = f"expected {shape.__qualname__}"
    return problems


# An AC is always commanded with an AcState; a bare on/off aimed at one becomes one here.
def ac_state(members: tuple[m.DeviceEnum, ...], state: Any, enums: Mapping[str, type[m.DeviceEnum]], *, source: str) -> Any:
    ac_cls = enums.get("AC")
    acs = tuple(of_type(members, ac_cls)) if ac_cls else ()
    if isinstance(state, m.AcState) and len(acs) != len(members):
        raise ValueError(f"AC command {state} applies only to AC devices, got {source!r}")
    elif not isinstance(state, m.AcState) and acs and state not in (m.ON, m.OFF):
        raise ValueError(f"AC devices take a mode:fan:temp command, 'on', or 'off', got {state!r}")
    elif acs and state in (m.ON, m.OFF):
        return m.AcState(power=state)
    return state


def load_plugin_config(
    name: str,
    config: Any,
    grammar: str,
    serializers: Mapping[str, scalar | group | array | raw | each],
) -> SimpleNamespace:
    text = config.plugin_configs[name]
    # Seed the parse with the sealed device registry so cast.devices/cast.device
    # resolve in plugin configs the same way they do in the main config; a parse
    # from declare() runs before the registry exists and gets no devices.
    registry = getattr(config, "registry", None)
    device = raw(lambda rows, objects: SimpleNamespace(enums=dict(registry.devices.items()) if registry else {}))
    return SimpleNamespace(**command_cfg.load(text, grammar, {"device": device, **serializers}, variables=os.environ))


def resolve_backend(value: ModuleType | None) -> ModuleType:
    if value is None:
        raise ConfigError("plugin has no --backend configured")
    return value


def _command(objects: dict[str, Any], args: SimpleNamespace, tag: em.Condition | None = None) -> em.Command[em.Condition, m.Devices]:
    devices = cast.devices(args.devices, objects)
    state = ac_state(devices.all(), cast.state(args.state), objects["device"].enums, source=args.devices)
    return em.Command[em.Condition, m.Devices](devices, state, tag=tag)


def _condition(objects: dict[str, Any], args: SimpleNamespace) -> em.Condition:
    person, weather = args.trigger_person, args.trigger_weather
    if person and weather:
        raise ValueError("A routine line takes --trigger-person or --trigger-weather, not both")
    elif weather:
        return m.Weather(m.WeatherCondition(weather))
    elif person is None:
        return em.And()
    elif person == "SYSTEM":
        return m.Always()
    elif person == "ANYONE":
        return m.Anyone()
    elif person in objects["person"]:
        return m.Present(person)
    else:
        raise ValueError(f"Unknown person {person!r}: expected one of {('SYSTEM', 'ANYONE', *objects['person'])}")


def _build_enum(objects: dict[str, Any], type_name: str, zigbee_config: dict[Any, tuple[Any, ...]]) -> type[m.DeviceEnum]:
    rows = objects["device"].members[type_name]
    for label, idx in (("names", 0), ("device id", 1)):
        vals = [r[idx] for r in rows]
        if duplicates := {v for v in vals if vals.count(v) > 1}:
            raise ValueError(f"Duplicate {label} in '{type_name}': {duplicates}")
    hub = type_name in ("Light", "Button", "Sensor")
    if hub:
        members = {name: (*zigbee_config.get(target, (uuid.uuid4().hex, frozenset())), room, label) for name, target, room, label in rows}
    else:
        members = {name: (target, frozenset(), room, label) for name, target, room, label in rows}
    # functional Enum API: mypy checks against the member-level __new__ rather than EnumMeta.__call__
    enum: type[m.DeviceEnum] = m.DeviceEnum(type_name, members, module="orc")  # type: ignore[call-arg,arg-type,assignment]
    if hub:
        objects["device"].virtual_devices.update(enum[name] for name, target, _, _ in rows if target not in zigbee_config)
    if (sort := objects["device"].sorts.get(type_name)) is not None:
        enum._sort = sort
    return enum


def _device(zigbee_config: dict[Any, tuple[Any, ...]], objects: dict[str, Any], args: SimpleNamespace) -> None:
    members = objects["device"].members
    enums = objects["device"].enums
    if args.type in enums:
        raise ValueError(f"Device type {args.type!r} is already sealed")
    elif args.define:
        members[args.type] = []
        objects["device"].sorts[args.type] = args.sort
    elif args.only:
        members[args.type] = [(args.id, args.target, args.room, args.name or args.id)] if args.id else []
        objects["device"].sorts[args.type] = args.sort
        enums[args.type] = _build_enum(objects, args.type, zigbee_config)
    elif args.type not in members:
        raise ValueError(f"Unknown device type {args.type!r}: expected one of {list(members)}")
    elif args.seal:
        enums[args.type] = _build_enum(objects, args.type, zigbee_config)
    else:
        members[args.type].append((args.id, args.target, args.room, args.name or args.id))


def _room(objects: dict[str, Any], args: SimpleNamespace) -> None:
    rooms = objects["room"]
    base = rooms.get(args.name, em.Action())
    rooms[args.name] = replace(base, commands=(*base.commands, _command(objects, args)))


def _ad_hoc(objects: dict[str, Any], args: SimpleNamespace) -> None:
    ad_hoc_routines = objects["ad_hoc"]
    if args.define:
        config = m.AdhocAction(
            name=args.name,
            snapshot=timedelta(minutes=args.snapshot) if args.snapshot is not None else None,
            delay=timedelta(minutes=args.delay) if args.delay is not None else timedelta(),
            section=cast.section(args.section),
            reset=not args.no_reset,
        )
    elif (config := ad_hoc_routines.get(args.id)) is None:
        raise ValueError(f"Unknown ad-hoc routine {args.id!r}: expected one of {tuple(ad_hoc_routines)}")
    if args.devices is not None:
        config = replace(config, commands=(*config.commands, _command(objects, args)))
    ad_hoc_routines[args.id] = config


def _remote(objects: dict[str, Any], args: SimpleNamespace) -> None:
    if args.event not in _BUTTON_EVENTS:
        raise ValueError(f"Invalid button event {args.event!r}: expected one of {sorted(_BUTTON_EVENTS)}")
    objects["remote"] = (*objects["remote"], m.Remote(cast.device(args.device, objects), args.button, args.event, args.action))


def _group(objects: dict[str, Any], args: SimpleNamespace) -> None:
    objects["group"] = (*objects["group"], m.ButtonGroup(args.id, args.title))


def _button(objects: dict[str, Any], args: SimpleNamespace) -> None:
    if (args.stripe_from is None) != (args.stripe_to is None):
        raise ValueError(f"Button {args.id!r} needs both --stripe-from and --stripe-to, or neither")
    objects["button"] = (*objects["button"], m.SceneButton(args.id, args.group, args.stripe_from, args.stripe_to))


def _plugin(objects: dict[str, Any], args: SimpleNamespace) -> None:
    params = {key: value for key, value in (("section", args.section), ("icon", args.icon), ("backend", args.backend)) if value is not None}
    if "section" in params:
        params["section"] = cast.section(params["section"])
    if args.function:
        func = engine_cast.resolve_function(f"{args.module.__name__}.{args.function}")
        objects["plugin"].append(m.CallablePlugin(name=args.name, module=args.module, func=func, **params))
    else:
        objects["plugin"].append(m.Plugin(name=args.name, module=args.module, **params))


def _routine(objects: dict[str, Any], args: SimpleNamespace) -> None:
    routines = objects["routine"]
    if args.define:
        tags = frozenset({m.SKIP_REPLAY_TAG}) if args.skip_replay else frozenset()
        routines[args.id] = em.Rule((), name=args.name, tags=tags)
    elif (routine := routines.get(args.id)) is None:
        raise ValueError(f"Unknown routine {args.id!r}: expected one of {tuple(routines)}")
    else:
        condition = _condition(objects, args)
        step = em.Step(condition, _command(objects, args, condition))
        routines[args.id] = replace(routine, steps=(*routine.steps, step))


def _theme(objects: dict[str, Any], args: SimpleNamespace) -> None:
    if (rule := objects["routine"].get(args.routine)) is None:
        raise ValueError(f"Unknown routine {args.routine!r}: expected one of {tuple(objects['routine'])}")
    theme = objects["theme"].setdefault(args.name, m.Theme(args.name))
    theme.entries = (*theme.entries, m.ThemeEntry(args.time, rule, args.routine))
