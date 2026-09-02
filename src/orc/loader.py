import importlib
import re
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import time, timedelta
from functools import partial
from types import MappingProxyType, ModuleType, SimpleNamespace
from typing import Any

import command_cfg
from command_cfg import ConfigError, array, each, group, raw, scalar

from orc import model as m
from orc.dal import interfaces
from orc.security import safe_eval

_BUTTON_EVENTS = frozenset({"pushed", "held", "doubleTapped", "released"})
_NO_OBJECTS: Mapping[str, Any] = MappingProxyType({})
_YOUTUBE_ID_RE = r"^[0-9A-Za-z_-]{11}$"
_ERR_STATE = "Invalid state {!r}: expected one of 'on', 'off', 'stop', 'pause', 'resume', an integer, or an 11-character YouTube ID"

GRAMMAR = """
ad_hoc define <name> [--snapshot=<minutes>] [--delay=<minutes>] [--section=<section>] [--no-reset] [<devices> <state>]
ad_hoc append <name> <devices> <state>

remote <device> <button> <event> <action>

device define <type>
device add <type> <id> <host> [--room=<room>] [--name=<name>]
device only <type> [<id> <host>] [--room=<room>] [--name=<name>]
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

theme <name> <routine> <time>
"""


def parse_config(text: str, zigbee_config: dict[Any, tuple[Any, ...]] | None = None) -> SimpleNamespace:
    serializers = {
        "person": group(m.Person),
        "device": each(partial(_device, zigbee_config or {}), default=lambda: SimpleNamespace(members={}, enums={})),
        "room": each(_room, default=dict),
        "ad_hoc": each(_ad_hoc, default=dict, types={"snapshot": int, "delay": int}),
        "remote": each(_remote, default=dict, types={"button": int}),
        "routine": each(_routine, default=dict),
        "highlight": each(_highlight, default=tuple, types={"start": Cast.when, "stop": Cast.when}),
        "theme": each(_theme, default=dict, types={"time": Cast.when}),
        "plugin": each(_plugin, default=list, types={"module": Cast.module, "backend": Cast.module}),
        "provider": scalar(interfaces.Provider, types={field: Cast.module for field in interfaces.Provider._fields}),
        "setting": scalar(
            m.Settings.build,
            types={
                "lat": Cast.float,
                "long": Cast.float,
                "http_timeout": Cast.int,
                "port": Cast.int,
                "warning_device": Cast.device,
                "attention_device": Cast.device,
                "emergency_device": Cast.device,
            },
        ),
    }
    objects = command_cfg.parse(text, GRAMMAR, serializers)
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
    if config.setting.emergency_routine not in config.routine:
        raise ConfigError(f"Unknown routine {config.setting.emergency_routine!r}: expected one of {tuple(config.routine)}")


_ERR_PARAMS = "Invalid parameter {}={!r}"
# "device" plugins are invoked per-device from the /device grid (via /api/run?device=…);
# they render no button and are not auto-invoked, unlike the other sections.
_VALID_SECTIONS = frozenset({"scene", "system", "device"})
_ERR_FUNCTION = (
    "Cannot load function {!r}: {}. Expected a fully qualified callable like 'orc.plugins.my_plugin'. "
    "Ensure the module exists and the function is defined within it."
)
_ERR_MODULE = "Cannot load module {!r}: {}. Expected an importable module like 'orc.dal.mqtt.stub'."


def resolve_device(value: str, devices: Mapping[str, type[m.DeviceEnum]]) -> m.Devices:
    try:
        return m.Devices(safe_eval(value, dict(devices)))
    except NameError as exc:
        raise ValueError(f"{exc} — device types must be defined and sealed first") from None
    except AttributeError:
        type_name, _, member = value.partition(".")
        options = sorted(devices[type_name].__members__) if type_name in devices else []
        raise ValueError(f"Unknown {type_name} device {member!r}: expected one of {options}") from None
    except SyntaxError as exc:
        raise ValueError(str(exc)) from None


class Cast:
    @staticmethod
    def devices(value: str, objects: Mapping[str, Any] = _NO_OBJECTS) -> m.Devices:
        return resolve_device(value, objects["device"].enums)

    @staticmethod
    def device(value: str, objects: Mapping[str, Any] = _NO_OBJECTS) -> m.DeviceEnum:
        return resolve_device(value, objects["device"].enums).one()

    @staticmethod
    def state(value: str) -> Any:
        if value in (m.ON, m.OFF, m.STOP, m.PAUSE, m.RESUME):
            return value
        elif re.match(_YOUTUBE_ID_RE, value):
            return m.YouTubeId(value)
        elif value.isdigit():
            return int(value)
        raise ValueError(_ERR_STATE.format(value))

    @staticmethod
    def when(value: str) -> time | str:
        return m.resolve_time(value)

    @staticmethod
    def clock(value: str) -> time:
        parsed = m.resolve_time(value)
        if isinstance(parsed, str):
            raise ValueError(f"Invalid time {value!r}: expected HH:MM")
        return parsed

    # module/float/int/device take an optional trailing `objects` so the same
    # caster works both for each()'s 1-arg coerce() and scalar()'s 2-arg (value, objects) call.
    @staticmethod
    def module(value: str, objects: Mapping[str, Any] = _NO_OBJECTS) -> ModuleType:
        try:
            return importlib.import_module(value)  # nosemgrep: non-literal-import
        except Exception as exc:
            raise ValueError(_ERR_MODULE.format(value, exc)) from exc

    @staticmethod
    def float(value: str, objects: Mapping[str, Any] = _NO_OBJECTS) -> float:
        return float(value)

    @staticmethod
    def int(value: str, objects: Mapping[str, Any] = _NO_OBJECTS) -> int:
        return int(value)

    @staticmethod
    def section(value: str | None) -> str | None:
        if value is None:
            return None
        elif value in _VALID_SECTIONS:
            return value
        raise ValueError(_ERR_PARAMS.format("section", value))


def _config(objects: dict[str, Any], args: SimpleNamespace, **extra: Any) -> m.Config:
    return m.Config(Cast.devices(args.devices, objects), Cast.state(args.state), **extra)


def _resolve_function(value: str) -> Callable[..., Any]:
    try:
        module_path, fn_name = value.rsplit(".", 1)
        return getattr(importlib.import_module(module_path), fn_name)  # nosemgrep: non-literal-import
    except Exception as exc:
        raise ValueError(_ERR_FUNCTION.format(value, exc)) from exc


def _build_enum(objects: dict[str, Any], type_name: str, zigbee_config: dict[Any, tuple[Any, ...]]) -> type[m.DeviceEnum]:
    rows = objects["device"].members[type_name]
    for label, idx in (("names", 0), ("device id", 1)):
        vals = [r[idx] for r in rows]
        if duplicates := {v for v in vals if vals.count(v) > 1}:
            raise ValueError(f"Duplicate {label} in '{type_name}': {duplicates}")
    if type_name in ("Light", "Button"):
        members = {
            name: (*zigbee_config.get(host, (-(i + 1), frozenset())), room, label) for i, (name, host, room, label) in enumerate(rows)
        }
    else:
        members = {name: (host, frozenset(), room, label) for name, host, room, label in rows}
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
        members[args.type] = [(args.id, args.host, args.room, args.name or args.id)] if args.id else []
        enums[args.type] = _build_enum(objects, args.type, zigbee_config)
    elif args.type not in members:
        raise ValueError(f"Unknown device type {args.type!r}: expected one of {list(members)}")
    elif args.seal:
        enums[args.type] = _build_enum(objects, args.type, zigbee_config)
    else:
        members[args.type].append((args.id, args.host, args.room, args.name or args.id))


def _room(objects: dict[str, Any], args: SimpleNamespace) -> None:
    configs = objects["room"].setdefault(args.name, m.Configs())
    configs.items = (*configs.items, _config(objects, args))


def _ad_hoc(objects: dict[str, Any], args: SimpleNamespace) -> None:
    ad_hoc_routines = objects["ad_hoc"]
    if args.define:
        ad_hoc_routines[args.name] = m.AdhocConfig(
            snapshot=timedelta(minutes=args.snapshot) if args.snapshot is not None else None,
            delay=timedelta(minutes=args.delay) if args.delay is not None else timedelta(),
            section=Cast.section(args.section) or "scene",
            reset=not args.no_reset,
        )
        if args.devices is not None:
            ad_hoc_routines[args.name].items = (_config(objects, args),)
    elif (config := ad_hoc_routines.get(args.name)) is None:
        raise ValueError(f"Unknown ad-hoc routine {args.name!r}: expected one of {tuple(ad_hoc_routines)}")
    else:
        config.items = (*config.items, _config(objects, args))


def _remote(objects: dict[str, Any], args: SimpleNamespace) -> None:
    if args.event not in _BUTTON_EVENTS:
        raise ValueError(f"Invalid button event {args.event!r}: expected one of {sorted(_BUTTON_EVENTS)}")
    objects["remote"][(Cast.device(args.device, objects), args.button, args.event)] = args.action


def _highlight(objects: dict[str, Any], args: SimpleNamespace) -> None:
    if args.name not in objects["ad_hoc"]:
        raise ValueError(f"Unknown ad-hoc routine {args.name!r}: expected one of {tuple(objects['ad_hoc'])}")
    objects["highlight"] = (*objects["highlight"], (args.name, args.start, args.stop))


def _plugin(objects: dict[str, Any], args: SimpleNamespace) -> None:
    params = {key: value for key, value in (("section", args.section), ("icon", args.icon), ("backend", args.backend)) if value is not None}
    if "section" in params:
        params["section"] = Cast.section(params["section"])
    if args.function:
        func = _resolve_function(f"{args.module.__name__}.{args.function}")
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
        routine.items = (*routine.items, _config(objects, args, trigger=args.trigger))


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
    return SimpleNamespace(**command_cfg.parse(text, grammar, serializers))


def resolve_backend(value: ModuleType | None) -> ModuleType:
    if value is None:
        raise ConfigError("plugin has no --backend configured")
    return value
