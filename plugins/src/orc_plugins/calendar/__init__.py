import sys
from typing import Any, NamedTuple

from orc_plugins.calendar import plugins

from orc import model as m
from orc.loader import load_plugin_config
from orc.model import AppContext

CONFIG = "orc_plugins/calendar"
GRAMMAR = """
setting <key> <value>
feed <name> <secret>
"""


class Settings(NamedTuple):
    backend: str
    cron: str
    window_hours: int
    max_events: int
    warning_minutes: int
    http_timeout: int


class Feed(NamedTuple):
    secret: str


def declare(declarations: Any) -> None:
    declarations.declare(setup=[setup])


def setup(ctx: AppContext) -> None:
    try:
        calendar = load_plugin_config(
            CONFIG,
            ctx.config.plugin_configs,
            GRAMMAR,
            serializers={"setting": Settings, "feed": Feed},
            scalars=("setting",),
            grouped=("feed",),
        )
        backend = m.column_to_value("module", calendar.setting.backend)
        feeds = [(name, feed.secret) for name, rows in calendar.feed.items() for feed in rows]
    except Exception as exc:
        print(f"Failed to load plugin config {CONFIG!r}: {exc}", file=sys.stderr)
        return
    plugins.schedule_cron(ctx, backend, calendar.setting, feeds)
