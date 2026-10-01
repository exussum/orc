import os
from collections.abc import Callable, Mapping
from dataclasses import asdict, replace
from datetime import datetime, timedelta, tzinfo
from functools import partial
from types import ModuleType, SimpleNamespace
from typing import Any

import command_cfg
from command_cfg import ConfigError, array, each, group, raw, scalar

from orc import model as m
from orc.dal import interfaces
from orc.kernel import cast, engine

_BUTTON_EVENTS = frozenset({"pushed", "held", "doubleTapped", "released"})
_WEATHER_TRIGGERS = frozenset(wc.value for wc in m.WeatherCondition)

_POLAR_LATITUDE = 65.7

GRAMMAR = """
ad_hoc define <name> [--snapshot=<minutes>] [--delay=<minutes>] [--section=<section>] [--no-reset] [<devices> <state>]
ad_hoc append <name> <devices> <state>

remote <device> <button> <event> <action>

device define <type> [--sort=<n>]
device add <type> <id> <target> [--room=<room>] [--name=<name>]
device only <type> [<id> <target>] [--room=<room>] [--name=<name>] [--sort=<n>]
device seal <type>

highlight <name> <start> <stop>

person <name> <host> <mac>

plugin <name> <module> [--section=<section>] [--icon=<icon>] [--backend=<module>]
plugin <name> <module> <function> --section=<section> [--icon=<icon>] [--backend=<module>]

provider <key> <module>

room <name> <devices> <state>

routine define <id> <name> [--skip-replay]
routine append <id> <devices> <state> [--trigger=<trigger>]

setting <key> <value>

tag <person> <secret> <pair_date>

theme <name> <routine> <time>
"""


def parse_config(text: str, zigbee_config: dict[Any, tuple[Any, ...]] | None = None) -> SimpleNamespace:
    serializers = {
        "person": group(m.Person),
        "device": each(
            partial(_device, zigbee_config or {}), default=lambda: SimpleNamespace(members={}, enums={}, sorts={}), types={"sort": int}
        ),
        "room": each(_room, default=dict),
        "ad_hoc": each(_ad_hoc, default=dict, types={"snapshot": int, "delay": int}),
        "remote": each(_remote, default=tuple, types={"button": int}),
        "routine": each(_routine, default=dict),
        "highlight": each(_highlight, default=tuple, types={"start": cast.when, "stop": cast.when}),
        "theme": each(_theme, default=dict, types={"time": cast.when}),
        "plugin": each(_plugin, default=list, types={"module": cast.module, "backend": cast.module}),
        "provider": scalar(interfaces.Provider, types={field: cast.module for field in interfaces.Provider._fields}),
        "setting": scalar(
            m.Settings.build,
            types={
                "lat": cast.float,
                "long": cast.float,
                "http_timeout": cast.int,
                "port": cast.int,
                "presence_hours": cast.int,
                "checkin_hours": cast.int,
                "sunset_lead_hours": cast.int,
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
        highlight=objects["highlight"],
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
        keys[tag.person] = m.BleKey(cast.hex32(secrets.other[tag.secret]), int(pair_date.timestamp()))
    return keys


def secret_needs(registry: m.Registry, providers: interfaces.Provider, tags: list[m.BleTag]) -> dict[str, Callable[[str], Any]]:
    needs: dict[str, Callable[[str], Any]] = {}
    for backend in providers:
        if backend:
            needs |= backend.REQUIRED_SECRETS
    needs |= registry.secrets
    needs |= {tag.secret: cast.hex32 for tag in tags}
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


def validate_ac_state(members: tuple[m.DeviceEnum, ...], state: Any, enums: Mapping[str, type[m.DeviceEnum]], *, source: str) -> None:
    ac_cls = enums.get("AC")
    acs = tuple(d for d in members if isinstance(d, ac_cls)) if ac_cls else ()
    if isinstance(state, m.AcCommand) and len(acs) != len(members):
        raise ValueError(f"AC command {state} applies only to AC devices, got {source!r}")
    elif not isinstance(state, m.AcCommand) and acs and state not in (m.ON, m.OFF):
        raise ValueError(f"AC devices take a mode:fan:temp command, 'on', or 'off', got {state!r}")


def _command(objects: dict[str, Any], args: SimpleNamespace, trigger: str | None = None) -> engine.Command[str, m.Devices]:
    devices = cast.devices(args.devices, objects)
    state = cast.state(args.state)
    validate_ac_state(devices.all(), state, objects["device"].enums, source=args.devices)
    return engine.Command[str, m.Devices](devices, state, tag=trigger)


def _conditions(trigger: str | None) -> tuple[engine.Condition, ...]:
    if trigger in (None, m.Tag.SYSTEM):
        return ()
    elif trigger in _WEATHER_TRIGGERS:
        return (engine.In(m.WeatherSubject(), m.WeatherCondition(trigger)),)
    elif trigger == m.Tag.ANYONE:
        return (engine.Is(m.AnyoneSubject(), True),)
    else:
        return (engine.Is(m.PersonSubject(trigger), True),)


def _clause(objects: dict[str, Any], args: SimpleNamespace, trigger: str | None) -> engine.Step[m.Devices]:
    return engine.Step(_conditions(trigger), _command(objects, args, trigger))


def _build_enum(objects: dict[str, Any], type_name: str, zigbee_config: dict[Any, tuple[Any, ...]]) -> type[m.DeviceEnum]:
    rows = objects["device"].members[type_name]
    for label, idx in (("names", 0), ("device id", 1)):
        vals = [r[idx] for r in rows]
        if duplicates := {v for v in vals if vals.count(v) > 1}:
            raise ValueError(f"Duplicate {label} in '{type_name}': {duplicates}")
    if type_name in ("Light", "Button", "Sensor"):
        members = {
            name: (*zigbee_config.get(target, (-(i + 1), frozenset())), room, label) for i, (name, target, room, label) in enumerate(rows)
        }
    else:
        members = {name: (target, frozenset(), room, label) for name, target, room, label in rows}
    # functional Enum API: mypy checks against the member-level __new__ rather than EnumMeta.__call__
    enum: type[m.DeviceEnum] = m.DeviceEnum(type_name, members, module="orc")  # type: ignore[call-arg,arg-type,assignment]
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
    base = rooms.get(args.name, engine.Action())
    rooms[args.name] = replace(base, commands=(*base.commands, _command(objects, args)))


def _ad_hoc(objects: dict[str, Any], args: SimpleNamespace) -> None:
    ad_hoc_routines = objects["ad_hoc"]
    if args.define:
        config = m.AdhocAction(
            snapshot=timedelta(minutes=args.snapshot) if args.snapshot is not None else None,
            delay=timedelta(minutes=args.delay) if args.delay is not None else timedelta(),
            section=cast.section(args.section),
            reset=not args.no_reset,
        )
    elif (config := ad_hoc_routines.get(args.name)) is None:
        raise ValueError(f"Unknown ad-hoc routine {args.name!r}: expected one of {tuple(ad_hoc_routines)}")
    if args.devices is not None:
        config = replace(config, commands=(*config.commands, _command(objects, args)))
    ad_hoc_routines[args.name] = config


def _remote(objects: dict[str, Any], args: SimpleNamespace) -> None:
    if args.event not in _BUTTON_EVENTS:
        raise ValueError(f"Invalid button event {args.event!r}: expected one of {sorted(_BUTTON_EVENTS)}")
    objects["remote"] = (*objects["remote"], m.Remote(cast.device(args.device, objects), args.button, args.event, args.action))


def _highlight(objects: dict[str, Any], args: SimpleNamespace) -> None:
    if args.name not in objects["ad_hoc"]:
        raise ValueError(f"Unknown ad-hoc routine {args.name!r}: expected one of {tuple(objects['ad_hoc'])}")
    objects["highlight"] = (*objects["highlight"], m.Highlight(args.name, args.start, args.stop))


def _plugin(objects: dict[str, Any], args: SimpleNamespace) -> None:
    params = {key: value for key, value in (("section", args.section), ("icon", args.icon), ("backend", args.backend)) if value is not None}
    if "section" in params:
        params["section"] = cast.section(params["section"])
    if args.function:
        func = cast.resolve_function(f"{args.module.__name__}.{args.function}")
        objects["plugin"].append(m.CallablePlugin(name=args.name, module=args.module, func=func, **params))
    else:
        objects["plugin"].append(m.Plugin(name=args.name, module=args.module, **params))


def _routine(objects: dict[str, Any], args: SimpleNamespace) -> None:
    routines = objects["routine"]
    if args.define:
        tags = frozenset({m.SKIP_REPLAY_TAG}) if args.skip_replay else frozenset()
        routines[args.id] = engine.Rule((), name=args.name, tags=tags)
    elif (routine := routines.get(args.id)) is None:
        raise ValueError(f"Unknown routine {args.id!r}: expected one of {tuple(routines)}")
    else:
        known = (None, *(t.value for t in m.Tag), *(w.value for w in m.WeatherCondition), *objects["person"])
        if args.trigger not in known:
            raise ValueError(f"Unknown trigger {args.trigger!r}: expected one of {known[1:]}")
        routines[args.id] = replace(routine, steps=(*routine.steps, _clause(objects, args, args.trigger)))


def _theme(objects: dict[str, Any], args: SimpleNamespace) -> None:
    if (routine := objects["routine"].get(args.routine)) is None:
        raise ValueError(f"Unknown routine {args.routine!r}: expected one of {tuple(objects['routine'])}")
    theme = objects["theme"].setdefault(args.name, m.Theme(args.name))
    theme.entries = (*theme.entries, m.ThemeEntry(args.time, routine))


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
