import itertools
from collections import defaultdict, deque
from dataclasses import KW_ONLY, dataclass, replace
from datetime import datetime, time
from enum import Enum
from itertools import chain
from typing import Callable, Tuple

from mistletoe.block_token import Heading, Table


class LogSource(str, Enum):
    SCHEDULED = "scheduled"
    REMOTE = "remote"
    MANUAL = "manual"


@dataclass
class LogEntry:
    timestamp: datetime
    source: LogSource
    action: str


class ActivityLog:
    def __init__(self):
        self.entries = deque(maxlen=200)

    def add(self, when, source, action):
        self.entries.appendleft(LogEntry(when, source, action))


@dataclass
class CalendarEvent:
    uuid: str
    summary: str
    datetime: datetime
    type: str

    @staticmethod
    def from_cal(cal, type, offset, tz):
        return CalendarEvent(
            cal.uid.to_ical().decode() + " " + type,
            cal.summary.to_ical().decode("utf-8"),
            cal.start.astimezone(tz) + offset,
            type,
        )


@dataclass
class CalendarJob:
    play: Callable[[], None]

    def __call__(self) -> None:
        self.play()


@dataclass
class IotJob:
    run: Callable[[bool], None]

    def __call__(self, force: bool = False) -> None:
        self.run(force)


@dataclass
class Config:
    what: object
    state: object
    _: KW_ONLY
    mandatory: bool = False


@dataclass
class Configs:
    items: Tuple[Config]

    def __init__(self, *items: Config) -> None:
        self.items = tuple(items)


@dataclass
class Routine:
    name: str
    when: str
    items: Tuple[Config]
    _: KW_ONLY

    def __post_init__(self) -> None:
        if self.when and not isinstance(self.when, time) and ":" in self.when:
            self.when = _str_to_time(self.when)


@dataclass
class Theme:
    name: str
    configs: Tuple[Routine]

    def __init__(self, name: str, *configs: Routine) -> None:
        self.name = name
        self.configs = tuple(configs)


@dataclass
class Secrets:
    access_token: str
    market_holidays_url: str
    ics_url: str


def _str_to_time(x):
    parts = x.split(":") if x else []
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour, minute)


def doc_to_table(doc, section, columns):
    # Heading store their contents in a subsequent child element
    # https://github.com/miyuchina/mistletoe/issues/99
    idx = next(
        (i for (i, e) in enumerate(doc.children) if isinstance(e, Heading) and e.children[0].content == section),
        None,
    )
    if idx is None:
        raise ValueError(f"Section '{section}' not found in document")

    markdown_table = next((e for e in doc.children[idx + 1 :] if isinstance(e, Table)), None)
    if markdown_table is None:
        raise ValueError(f"No table found under section '{section}'")

    rows = list(markdown_table.children)
    invalid = [(i, len(row.children)) for i, row in enumerate(rows) if len(row.children) != columns]
    if invalid:
        details = ", ".join(f"row {i} has {count}" for i, count in invalid)
        raise ValueError(f"Expected {columns} columns in section '{section}', but: {details}")

    return tuple(tuple(c.children[0].content if c.children else None for c in e.children) for e in rows)


def doc_to_sub_tables(doc, section, columns):
    type, result = None, None
    for e in doc_to_table(doc, section, columns):
        if e[0] != type and e[0]:
            if result:
                yield type, result
            type, result = e[0], []
        result.append(e)

    if result:
        yield type, result


def build_enum(doc, section, sub_section, hubitat_config):
    if sub_section not in ("Light", "Sound"):
        raise ValueError(f"sub_section must be 'Light' or 'Sound', got '{sub_section}'")

    sub_table = next((sub_table for (type, sub_table) in doc_to_sub_tables(doc, section, 3) if type == sub_section))

    for label, idx in (("tokens", 1), ("names", 2)):
        vals = [e[idx] for e in sub_table]
        if duplicates := {v for v in vals if vals.count(v) > 1}:
            raise ValueError(f"Duplicate {label} in '{sub_section}': {duplicates}")

    hub_name_to_token = {e[2]: e[1] for e in sub_table}
    id_lookup = {e["label"]: int(e["id"]) for e in hubitat_config}

    result = Enum(
        sub_section,
        {token: id_lookup.get(name, -(default + 1)) for (default, (name, token)) in enumerate(hub_name_to_token.items())},
    )
    result.__class__.__sub__ = lambda self, e: set(self) - e
    return result


def _validate_states(sub_tables, col):
    return [(type, c[col]) for type, e in sub_tables for c in e if c[col] not in ("on", "off") and not c[col].isdigit()]


def build_themes(doc, routine_section, theme_section, light, sound):
    routine_tables = list(doc_to_sub_tables(doc, routine_section, 5))

    if invalid := _validate_states(routine_tables, 3):
        details = ", ".join(f"'{v}' in '{t}'" for t, v in invalid)
        raise ValueError(f"Invalid state values in section '{routine_section}': {details}")

    invalid_mandatory = [(type, c[4]) for type, e in routine_tables for c in e if c[4] not in ("True", "False", None, "")]
    if invalid_mandatory:
        details = ", ".join(f"'{v}' in '{t}'" for t, v in invalid_mandatory)
        raise ValueError(f"Invalid mandatory values in section '{routine_section}': {details}")

    theme_tables = list(doc_to_sub_tables(doc, theme_section, 3))

    for theme_type, e in theme_tables:
        for c in e:
            if not _str_to_time(c[2]) and c[2] not in ("sunrise", "sunset"):
                raise ValueError(f"Invalid time '{c[2]}' in theme '{theme_type}': expected HH:MM, 'sunrise', or 'sunset'")

    routines = {}
    for type, e in routine_tables:
        configs = [_build_config(c[2], sound, light, c[3], c[4]) for c in e]
        routines[type] = Routine(e[0][1], "", configs)

    themes = {type: Theme(type, *[replace(routines[c[1]], when=c[2]) for c in e]) for type, e in theme_tables}

    if missing := {"work day", "day off"} - themes.keys():
        raise ValueError(f"Missing required themes in section '{theme_section}': {', '.join(sorted(missing))}")

    return themes


def build_config(doc, section, light, sound):
    sub_tables = list(doc_to_sub_tables(doc, section, 3))
    if invalid := _validate_states(sub_tables, 2):
        details = ", ".join(f"'{v}' in '{t}'" for t, v in invalid)
        raise ValueError(f"Invalid state values in section '{section}': {details}")
    return {type: Configs(*[_build_config(c[1], sound, light, c[2]) for c in e]) for type, e in sub_tables}


def build_expr_config(doc, section, light, sound):
    result = {}
    for type, e in doc_to_sub_tables(doc, section, 2):
        result[type] = Configs(*itertools.chain(*(_build_config_from_expr(c[1], sound, light) for c in e if c[1])))
    return result


def build_highlights(doc, section):
    rows = doc_to_table(doc, section, 3)

    invalid = [(name, val) for (name, start, end) in rows for val in (start, end) if _str_to_time(val) is None]
    if invalid:
        details = ", ".join(f"'{v}' in '{n}'" for n, v in invalid)
        raise ValueError(f"Invalid time values in section '{section}': {details}")

    return [(name, _str_to_time(start), _str_to_time(end)) for (name, start, end) in rows]


def _build_config(cmd, sound, light, state, mandatory=None):
    if state not in ("on", "off"):
        state = int(state)
    mandatory = mandatory == "True"
    return Config(eval(cmd, {"__builtins__": {}}, {"Light": light, "Sound": sound}), state, mandatory=mandatory)


def _build_config_from_expr(cmd, sound, light):
    return eval(cmd, {"__builtins__": {"Config": Config, "tuple": tuple, "itertools": itertools}}, {"Light": light, "Sound": sound})


def squish_configs(*configs, state_override=None):
    """
    Take multiple Configs objects, and merge them into one as if they were run sequentially, removing duplicates
    and handling brightness changes.
    """
    rules = defaultdict(list)
    for routine in configs:
        for rule in routine.items:

            what = [rule.what] if isinstance(rule.what, Enum) else rule.what
            for e in what:
                rules[e].append(Config(what=e, state=rule.state if state_override is None else state_override))

    rules = list(chain.from_iterable(squish(e) for e in rules.values()))
    rules.sort(key=_op_cmp)
    return Configs(*rules)


def _op_cmp(k):
    class_name = k.what.__class__.__name__

    if isinstance(k.state, int):
        sub_sort = -1
    elif k.state == "on":
        sub_sort = 0
    else:
        sub_sort = 1
    return (class_name, sub_sort)


def squish(items):
    if not items:
        return ()

    result = (items[-1],)
    if isinstance(result[0].state, int):
        return result

    # Working backwards, use the next number state since
    # the first was either an ON or OFF.
    for e in range(len(items) - 2, -1, -1):
        if isinstance(items[e].state, int):
            result = (items[e], result[0])
            break
    return result
