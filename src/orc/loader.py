from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta
from functools import partial
from types import ModuleType, SimpleNamespace
from typing import Any

import command_cfg
from command_cfg import ConfigError, array, each, group, raw, scalar

from orc import model as m
from orc.dal import interfaces
from orc.security import safe_eval

_BUTTON_EVENTS = frozenset({"pushed", "held", "doubleTapped", "released"})

GRAMMAR = """
ad_hoc define <name> [--snapshot=<minutes>] [--delay=<minutes>] [--section=<section>] [--no-reset] [<device> <state>]
ad_hoc append <name> <device> <state>

remote <device> <button> <event> <action>

device define <type>
device add <type> <name> <host> [--room=<room>]
device only <type> [<name> <host>] [--room=<room>]
device seal <type>

highlight <name> <start> <stop>

person <name> <host> <mac>

plugin <name> <module> [--section=<section>] [--icon=<icon>] [--backend=<module>]
plugin <name> <module> <function> --section=<section> [--icon=<icon>] [--backend=<module>]

provider <key> <module>

room <name> <device> <state>

routine define <id> <name> [--skip-replay]
routine append <id> <device> <state> [--trigger=<trigger>]

setting <key> <value>

theme <name> <routine> <time>

volume <log> <level>
"""


def parse_config(text: str, zigbee_config: dict[Any, tuple[Any, ...]] | None = None) -> SimpleNamespace:
    serializers = {
        "person": group(m.Person),
        "device": each(partial(_device, zigbee_config or {}), default=lambda: SimpleNamespace(members={}, enums={})),
        "room": each(_room, default=dict),
        "ad_hoc": each(_ad_hoc, default=dict),
        "remote": each(_remote, default=dict),
        "routine": each(_routine, default=dict),
        "highlight": each(_highlight, default=tuple),
        "theme": each(_theme, default=dict),
        "plugin": each(_plugin, default=list),
        "volume": scalar(m.Volume),
        "provider": scalar(interfaces.Provider),
        "setting": scalar(m.Settings.build),
    }
    objects = command_cfg.parse(text, GRAMMAR, serializers, cast=lambda key, value, built: _cast(built, key, value))
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
        theme=objects["theme"],
        volume=objects["volume"],
    )


def validate(config: SimpleNamespace) -> None:
    for label, present, required in (
        ("routines", config.routine.keys(), ("ROUTINE_DEFAULT", "ROUTINE_RESET")),
        ("routine names", {r.name for r in config.routine.values()}, ("Reset",)),
        ("themes", config.theme.keys(), (m.THEME_WORK_DAY, m.THEME_DAY_OFF)),
    ):
        if missing := set(required) - present:
            raise ConfigError(f"Missing required {label}: {', '.join(sorted(missing))}")
    if unset := [key for key, value in zip(interfaces.Provider._fields, config.provider) if value is None]:
        raise ConfigError(f"Missing required providers: {', '.join(unset)}")
    if unset := [key for key, value in zip(m.Settings._fields, config.setting) if value in (None, "")]:
        raise ConfigError(f"Missing required settings: {', '.join(unset)}")


def _cast(objects: dict[str, Any], key: str, value: Any) -> Any:
    if not isinstance(value, str):
        return value
    elif key == "device":
        try:
            return safe_eval(value, dict(objects["device"].enums))
        except NameError as exc:
            raise ValueError(f"{exc} — device types must be defined and sealed first") from None
        except (AttributeError, SyntaxError) as exc:
            raise ValueError(str(exc)) from None
    elif key == "level":
        level = int(value)
        if not 0 <= level <= 100:
            raise ValueError(f"Invalid parameter level={value!r}")
        return level
    elif key == "function":
        return value
    return m.column_to_value(key, value)


def _build_enum(objects: dict[str, Any], type_name: str, zigbee_config: dict[Any, tuple[Any, ...]]) -> type[m.DeviceEnum]:
    rows = objects["device"].members[type_name]
    for label, idx in (("names", 0), ("device id", 1)):
        vals = [r[idx] for r in rows]
        if duplicates := {v for v in vals if vals.count(v) > 1}:
            raise ValueError(f"Duplicate {label} in '{type_name}': {duplicates}")
    if type_name in ("Light", "Button"):
        members = {name: (*zigbee_config.get(host, (-(i + 1), frozenset())), room) for i, (name, host, room) in enumerate(rows)}
    else:
        members = {name: (host, frozenset(), room) for name, host, room in rows}
    # functional Enum API: mypy checks against the member-level __new__ rather than EnumMeta.__call__
    return m.DeviceEnum(type_name, members, module="orc")  # type: ignore[call-arg,arg-type,return-value]


def _device(zigbee_config: dict[Any, tuple[Any, ...]], objects: dict[str, Any], args: SimpleNamespace) -> None:
    members = objects["device"].members
    enums = objects["device"].enums
    if args.type in enums:
        raise ValueError(f"Device type {args.type!r} is already sealed")
    elif args.define:
        members[args.type] = []
    elif args.only:
        members[args.type] = [(args.name, args.host, args.room)] if args.name else []
        enums[args.type] = _build_enum(objects, args.type, zigbee_config)
    elif args.type not in members:
        raise ValueError(f"Unknown device type {args.type!r}: expected one of {list(members)}")
    elif args.seal:
        enums[args.type] = _build_enum(objects, args.type, zigbee_config)
    else:
        members[args.type].append((args.name, args.host, args.room))


def _room(objects: dict[str, Any], args: SimpleNamespace) -> None:
    configs = objects["room"].setdefault(args.name, m.Configs())
    configs.items = (*configs.items, m.Config(args.device, args.state))


def _ad_hoc(objects: dict[str, Any], args: SimpleNamespace) -> None:
    ad_hoc_routines = objects["ad_hoc"]
    if args.define:
        ad_hoc_routines[args.name] = m.AdhocConfig(
            snapshot=args.snapshot,
            delay=args.delay or timedelta(),
            section=args.section or "scene",
            reset=not args.no_reset,
        )
        if args.device is not None:
            ad_hoc_routines[args.name].items = (m.Config(args.device, args.state),)
    elif (config := ad_hoc_routines.get(args.name)) is None:
        raise ValueError(f"Unknown ad-hoc routine {args.name!r}: expected one of {tuple(ad_hoc_routines)}")
    else:
        config.items = (*config.items, m.Config(args.device, args.state))


def _remote(objects: dict[str, Any], args: SimpleNamespace) -> None:
    if args.event not in _BUTTON_EVENTS:
        raise ValueError(f"Invalid button event {args.event!r}: expected one of {sorted(_BUTTON_EVENTS)}")
    elif not args.button.isdigit():
        raise ValueError(f"Invalid parameter button={args.button!r}")
    objects["remote"][(args.device, int(args.button), args.event)] = args.action


def _highlight(objects: dict[str, Any], args: SimpleNamespace) -> None:
    if args.name not in objects["ad_hoc"]:
        raise ValueError(f"Unknown ad-hoc routine {args.name!r}: expected one of {tuple(objects['ad_hoc'])}")
    objects["highlight"] = (*objects["highlight"], (args.name, args.start, args.stop))


def _plugin(objects: dict[str, Any], args: SimpleNamespace) -> None:
    params = {key: value for key, value in (("section", args.section), ("icon", args.icon), ("backend", args.backend)) if value is not None}
    if params.get("backend") is not None:
        params["backend"] = m.column_to_value("module", params["backend"])
    if args.function:
        func = m.column_to_value("function", f"{args.module.__name__}.{args.function}")
        objects["plugin"].append(m.CallablePlugin(name=args.name, module=args.module, func=func, **params))
    else:
        objects["plugin"].append(m.Plugin(name=args.name, module=args.module, **params))


def _routine(objects: dict[str, Any], args: SimpleNamespace) -> None:
    routines = objects["routine"]
    if args.define:
        routines[args.id] = m.Routine(args.name, "", (), skip_replay=args.skip_replay)
    elif (routine := routines.get(args.id)) is None:
        raise ValueError(f"Unknown routine {args.id!r}: expected one of {tuple(routines)}")
    else:
        known = (None, *(t.value for t in m.Trigger), *(w.value for w in m.WeatherCondition), *objects["person"])
        if args.trigger not in known:
            raise ValueError(f"Unknown trigger {args.trigger!r}: expected one of {known[1:]}")
        routine.items = (*routine.items, m.Config(args.device, args.state, trigger=args.trigger))


def _theme(objects: dict[str, Any], args: SimpleNamespace) -> None:
    if (routine := objects["routine"].get(args.routine)) is None:
        raise ValueError(f"Unknown routine {args.routine!r}: expected one of {tuple(objects['routine'])}")
    theme = objects["theme"].setdefault(args.name, m.Theme(args.name))
    theme.configs = (*theme.configs, replace(routine, when=args.time))


def load_plugin_config(
    name: str,
    plugin_configs: dict[str, str],
    grammar: str,
    serializers: Mapping[str, scalar | group | array | raw | each],
) -> SimpleNamespace:
    text = plugin_configs.get(name)
    if text is None:
        raise FileNotFoundError(f"no config 'plugins/{name}.orc'")
    return SimpleNamespace(**command_cfg.parse(text, grammar, serializers, cast=_plugin_cast))


def _plugin_cast(key: str, value: Any, objects: Mapping[str, Any]) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return m.column_to_value(key, value)
    except (NameError, AttributeError, SyntaxError) as exc:
        raise ValueError(str(exc)) from None


def resolve_backend(value: ModuleType | None) -> ModuleType:
    if value is None:
        raise ConfigError("plugin has no --backend configured")
    return value
