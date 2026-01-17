from dataclasses import KW_ONLY, dataclass
from datetime import time
from enum import Enum
from typing import Optional, Tuple


@dataclass
class Config:
    _: KW_ONLY
    what: object
    state: object
    mandatory: bool = False


@dataclass
class AdHocConfig:
    items: Tuple[Config]


@dataclass
class RoutineConfig:
    _: KW_ONLY
    items: Tuple[Config]
    name: str
    when: str

    def __post_init__(self):
        if self.when and not isinstance(self.when, time) and ":" in self.when:
            hour, minute = tuple(self.when.split(":"))
            self.when = time(int(hour), int(minute))


@dataclass
class Theme:
    name: str
    configs: Tuple[RoutineConfig]


def scan(*themes):
    theme_names = set()

    for theme in themes:
        if theme.name in theme_names:
            raise ValueError(f"Theme name repeated: {theme.name}")
            theme_names.add(theme.name)

        for e in theme.configs:
            if isinstance(e, RoutineConfig):
                for w in e.items:
                    w.when = e.when

    return themes
