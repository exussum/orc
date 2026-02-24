import itertools
from collections import defaultdict
from dataclasses import KW_ONLY, dataclass, replace
from datetime import time
from enum import Enum
from itertools import chain
from typing import Tuple

from mistletoe.block_token import Heading, Table


@dataclass
class Config:
    what: object
    state: object
    _: KW_ONLY
    mandatory: bool = False


@dataclass
class Configs:
    items: Tuple[Config]

    def __init__(self, *items):
        self.items = tuple(items)


@dataclass
class Routine:
    name: str
    when: str
    items: Tuple[Config]
    _: KW_ONLY

    def __post_init__(self):
        if self.when and not isinstance(self.when, time) and ":" in self.when:
            hour, minute = tuple(self.when.split(":"))
            self.when = time(int(hour), int(minute))


@dataclass
class Theme:
    name: str
    configs: Tuple[Routine]

    def __init__(self, name, *configs):
        self.name = name
        self.configs = tuple(configs)


def doc_to_sub_tables(doc, section):
    # Heading store their contents in a subsequent child element
    # https://github.com/miyuchina/mistletoe/issues/99
    idx = next((i for (i, e) in enumerate(doc.children) if isinstance(e, Heading) and e.children[0].content == section))
    markdown_table = next((e for e in doc.children[idx + 1 :] if isinstance(e, Table)))
    table = tuple((tuple(c.children[0].content if c.children else None for c in e.children) for e in markdown_table.children))

    type, result = None, None
    for e in table:
        if e[0] != type and e[0]:
            if result:
                yield type, result
            type, result = e[0], []
        result.append(e)

    if result:
        yield type, result


def build_enum(doc, section, sub_section, hubitat_config):
    sub_table = next((sub_table for (type, sub_table) in doc_to_sub_tables(doc, section) if type == sub_section))

    hub_name_to_token = {e[2]: e[1] for e in sub_table}
    id_lookup = {e["label"]: int(e["id"]) for e in hubitat_config}

    result = Enum(
        sub_section,
        {token: id_lookup.get(name, -(default + 1)) for (default, (name, token)) in enumerate(hub_name_to_token.items())},
    )
    result.__class__.__sub__ = lambda self, e: set(self) - e
    return result


def build_themes(doc, routine_section, theme_section, light, sound):
    routines = {}

    for type, e in doc_to_sub_tables(doc, routine_section):
        configs = [Config(eval(c[2], {"__builtins__": {}}, {"Light": light, "Sound": sound}), c[3], mandatory=(c[4] == "True")) for c in e]
        routines[type] = Routine(e[0][1], "", configs)

    result = []
    for type, e in doc_to_sub_tables(doc, theme_section):
        result.append(Theme(type, *[replace(routines[c[1]], when=c[2]) for c in e]))
    return result


def build_config(doc, section, light, sound):
    result = {}
    for type, e in doc_to_sub_tables(doc, section):
        result[type] = Configs(*[Config(eval(c[1], {"__builtins__": {}}, {"Light": light, "Sound": sound}), c[2]) for c in e])
    return result


def build_expr_config(doc, section, light, sound):
    result = {}
    for type, e in doc_to_sub_tables(doc, section):
        result[type] = Configs(*itertools.chain(*(_run(c[1], sound, light) for c in e if c[1])))
    return result


def _run(cmd, sound, light):
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
    rules.sort(key=lambda e: isinstance(e.state, str))
    return Configs(*rules)


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
