from dataclasses import KW_ONLY, dataclass
from datetime import time
from enum import Enum
from typing import Optional, Tuple


@dataclass
class Config:
    what: object
    state: object
    _: KW_ONLY
    mandatory: bool = False


@dataclass
class Configs:
    items: Tuple[Config]


@dataclass
class Routine:
    name: str
    when: str
    items: Tuple[Config]

    def __post_init__(self):
        if self.when and not isinstance(self.when, time) and ":" in self.when:
            hour, minute = tuple(self.when.split(":"))
            self.when = time(int(hour), int(minute))


@dataclass
class Theme:
    name: str
    configs: Tuple[Routine]


def scan(*themes):
    theme_names = set()

    for theme in themes:
        if theme.name in theme_names:
            raise ValueError(f"Theme name repeated: {theme.name}")
            theme_names.add(theme.name)

        for e in theme.configs:
            if isinstance(e, Routine):
                for w in e.items:
                    w.when = e.when

    return themes
