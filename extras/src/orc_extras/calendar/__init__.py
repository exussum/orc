import sys
from typing import Any, NamedTuple

from command_cfg import array, scalar
from orc_extras.calendar import plugins

from orc import model as m
from orc.loader import load_plugin_config
from orc.model import AppContext

CONFIG = "orc_extras/calendar"
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
            serializers={"setting": scalar(Settings), "feed": array(Feed)},
        )
        backend = m.column_to_value("module", calendar.setting.backend)
    except Exception as exc:
        print(f"Failed to load plugin config {CONFIG!r}: {exc}", file=sys.stderr)
        return
    plugins.schedule_cron(ctx, backend, calendar.setting, calendar.feed)
