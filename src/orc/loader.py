from collections.abc import Callable
from dataclasses import replace
from functools import partial
from types import SimpleNamespace
from typing import Any

import command_cfg
from command_cfg import ConfigError

from orc import model as m
from orc.security import safe_eval

GRAMMAR = """
device define <type>
device add <type> <name> <host> [--room=<room>]
device only <type> [<name> <host>] [--room=<room>]
device seal <type>
room <name> <device> <state>
routine define <id> <name>
routine append <id> <device> <state> [--trigger=<trigger>]
theme <name> <routine> <time>
volume <log> <level>
"""


def parse_config(text: str, zigbee_config: dict[Any, tuple[Any, ...]] | None = None) -> SimpleNamespace:
    def run(handler: Callable[[dict[str, Any], SimpleNamespace], None], values: SimpleNamespace, objects: dict[str, Any]) -> None:
        objects.setdefault("zigbee_config", zigbee_config or {})
        handler(objects, SimpleNamespace(**{key: _cast(objects, key, value) for key, value in vars(values).items()}))

    objects = command_cfg.parse(text, GRAMMAR, {command: partial(run, handler) for command, handler in _COMMANDS.items()})
    if unsealed := objects.get("_members", {}).keys() - objects.get("enums", {}).keys():
        raise ConfigError(f"Device types defined but never sealed: {sorted(unsealed)}")
    return SimpleNamespace(
        audio_volumes=objects.get("audio_volumes", {}),
        enums=objects.get("enums", {}),
        routines=objects.get("routines", {}),
        themes=objects.get("themes", {}),
        room_configs=objects.get("room_configs", {}),
    )


def validate(config: SimpleNamespace) -> None:
    for label, present, required in (
        ("routines", config.routines.keys(), ("ROUTINE_DEFAULT", "ROUTINE_RESET")),
        ("routine names", {r.name for r in config.routines.values()}, ("Reset",)),
        ("themes", config.themes.keys(), (m.THEME_WORK_DAY, m.THEME_DAY_OFF)),
        ("volumes", config.audio_volumes.keys(), (m.AUDIO_INFO, m.AUDIO_FATAL)),
    ):
        if missing := set(required) - present:
            raise ConfigError(f"Missing required {label}: {', '.join(sorted(missing))}")


def _cast(objects: dict[str, Any], key: str, value: Any) -> Any:
    if not isinstance(value, str):
        return value
    elif key == "device":
        # resolve against the enums built so far in this parse, not the installed package
        try:
            return safe_eval(value, dict(objects.get("enums", {})))
        except NameError as exc:
            raise ValueError(f"{exc} — device types must be defined and sealed first") from None
        except (AttributeError, SyntaxError) as exc:
            raise ValueError(str(exc)) from None
    elif key == "level":
        level = int(value)
        if not 0 <= level <= 100:
            raise ValueError(f"Invalid parameter level={value!r}")
        return level
    return m.column_to_value(key, value)


def _build_enum(objects: dict[str, Any], type_name: str) -> type[m.DeviceEnum]:
    rows = objects["_members"][type_name]
    for label, idx in (("names", 0), ("device id", 1)):
        vals = [r[idx] for r in rows]
        if duplicates := {v for v in vals if vals.count(v) > 1}:
            raise ValueError(f"Duplicate {label} in '{type_name}': {duplicates}")
    if type_name in ("Light", "Button"):
        members = {name: (*objects["zigbee_config"].get(host, (-(i + 1), frozenset())), room) for i, (name, host, room) in enumerate(rows)}
    else:
        members = {name: (host, frozenset(), room) for name, host, room in rows}
    # functional Enum API: mypy checks against the member-level __new__ rather than EnumMeta.__call__
    return m.DeviceEnum(type_name, members, module="orc")  # type: ignore[call-arg,arg-type,return-value]


def _device(objects: dict[str, Any], args: SimpleNamespace) -> None:
    members = objects.setdefault("_members", {})
    enums = objects.setdefault("enums", {})
    if args.type in enums:
        raise ValueError(f"Device type {args.type!r} is already sealed")
    elif args.define:
        members[args.type] = []
    elif args.only:
        members[args.type] = [(args.name, args.host, args.room)] if args.name else []
        enums[args.type] = _build_enum(objects, args.type)
    elif args.type not in members:
        raise ValueError(f"Unknown device type {args.type!r}: expected one of {list(members)}")
    elif args.seal:
        enums[args.type] = _build_enum(objects, args.type)
    else:
        members[args.type].append((args.name, args.host, args.room))


def _room(objects: dict[str, Any], args: SimpleNamespace) -> None:
    configs = objects.setdefault("room_configs", {}).setdefault(args.name, m.Configs())
    configs.items = (*configs.items, m.Config(args.device, args.state))


def _routine(objects: dict[str, Any], args: SimpleNamespace) -> None:
    routines = objects.setdefault("routines", {})
    if args.define:
        routines[args.id] = m.Routine(args.name, "", ())
    elif (routine := routines.get(args.id)) is None:
        raise ValueError(f"Unknown routine {args.id!r}: expected one of {tuple(routines)}")
    else:
        known = (None, *(t.value for t in m.Trigger), *(w.value for w in m.WeatherCondition))
        if args.trigger not in known:
            raise ValueError(f"Unknown trigger {args.trigger!r}: expected one of {known[1:]}")
        routine.items = (*routine.items, m.Config(args.device, args.state, trigger=args.trigger))


def _theme(objects: dict[str, Any], args: SimpleNamespace) -> None:
    routines = objects.setdefault("routines", {})
    if (routine := routines.get(args.routine)) is None:
        raise ValueError(f"Unknown routine {args.routine!r}: expected one of {tuple(routines)}")
    theme = objects.setdefault("themes", {}).setdefault(args.name, m.Theme(args.name))
    theme.configs = (*theme.configs, replace(routine, when=args.time))


def _volume(objects: dict[str, Any], args: SimpleNamespace) -> None:
    objects.setdefault("audio_volumes", {})[args.log] = args.level


_COMMANDS = {
    "device": _device,
    "room": _room,
    "routine": _routine,
    "theme": _theme,
    "volume": _volume,
}
