from typing import Any, NamedTuple

from command_cfg import array, scalar

from orc.kernel import cast
from orc.kernel.loader import load_plugin_config
from orc.model import AppContext
from orc_extras.calendar import plugins

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


_SERIALIZERS = {
    "setting": scalar(
        Settings, types={"window_hours": cast.int, "max_events": cast.int, "warning_minutes": cast.int, "http_timeout": cast.int}
    ),
    "feed": array(Feed),
}


def declare(declarations: Any) -> None:
    feeds = load_plugin_config(CONFIG, declarations, GRAMMAR, _SERIALIZERS).feed
    declarations.declare(setup=[setup], secrets={feed.secret: cast.url for feed in feeds})


def setup(ctx: AppContext) -> None:
    calendar = load_plugin_config(CONFIG, ctx.config, GRAMMAR, _SERIALIZERS)
    backend = cast.module(calendar.setting.backend)
    plugins.schedule_cron(ctx, backend, calendar.setting, calendar.feed)
