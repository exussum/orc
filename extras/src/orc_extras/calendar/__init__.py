from pathlib import Path
from typing import Any

from orc_engine import cast

from orc.kernel.loader import load_plugin_config
from orc.model import AppContext
from orc_extras.calendar import plugins, web
from orc_extras.calendar.plugins import CONFIG, GRAMMAR, SERIALIZERS


def declare(declarations: Any) -> None:
    feeds = load_plugin_config(CONFIG, declarations, GRAMMAR, SERIALIZERS).feed
    declarations.declare(
        setup=[setup],
        secrets={feed.secret: cast.url for feed in feeds},
        blueprints={"choose": web.choose},
        scripts=[Path(__file__).parent / "static" / "calendar.js"],
    )


def setup(ctx: AppContext) -> None:
    plugins.schedule_cron(ctx, load_plugin_config(CONFIG, ctx.config, GRAMMAR, SERIALIZERS).setting.cron)
