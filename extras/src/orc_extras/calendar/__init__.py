import sys
from typing import Any, NamedTuple

from command_cfg import array, scalar

from orc.loader import Cast, load_plugin_config
from orc.model import AppContext
from orc_extras.calendar import plugins

CONFIG = "orc_extras/calendar"
GRAMMAR = """
setting <key> <value>
feed <name> <secret>
"""

_SETTING_TYPES = {"window_hours": int, "max_events": int, "warning_minutes": int, "http_timeout": int}


class Settings(NamedTuple):
    backend: str
    cron: str
    window_hours: int
    max_events: int
    warning_minutes: int
    http_timeout: int


class Feed(NamedTuple):
    name: str
    secret: str


def declare(declarations: Any) -> None:
    declarations.declare(setup=[setup])


def setup(ctx: AppContext) -> None:
    try:
        calendar = load_plugin_config(
            CONFIG,
            ctx.config.plugin_configs,
            GRAMMAR,
            serializers={"setting": scalar(Settings, types=_SETTING_TYPES), "feed": array(Feed)},
        )
        backend = Cast.module(calendar.setting.backend)
    except Exception as exc:
        print(f"Failed to load plugin config {CONFIG!r}: {exc}", file=sys.stderr)
        return
    plugins.schedule_cron(ctx, backend, calendar.setting, calendar.feed)
