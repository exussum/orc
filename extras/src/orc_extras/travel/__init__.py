from pathlib import Path
from typing import Any

from command_cfg import array, scalar

from orc.loader import Cast, load_plugin_config
from orc.model import AppContext
from orc_extras.travel import plugins
from orc_extras.travel.dal import sqlite
from orc_extras.travel.model import Extra, Place, Runtime, Settings
from orc_extras.travel.web import travel_bp

CONFIG = "orc_extras/travel"
GRAMMAR = """
setting <key> <value>
place <name> <address>
extra <name> <minutes>
"""


def declare(declarations: Any) -> None:
    declarations.declare(setup=[setup], blueprints={"jobs": travel_bp}, scripts=[Path(__file__).parent / "static" / "travel.js"])


def setup(ctx: AppContext) -> None:
    cfg = load_plugin_config(
        CONFIG,
        ctx.config,
        GRAMMAR,
        {
            "setting": scalar(Settings, types={"window_hours": Cast.int, "http_timeout": Cast.int, "buffer_minutes": Cast.int}),
            "place": array(Place),
            "extra": array(Extra, types={"minutes": int}),
        },
    )
    s = cfg.setting
    runtime = Runtime(
        drive=Cast.module(s.drive_backend),
        flight=Cast.module(s.flight_backend),
        settings=s,
        extras=cfg.extra,
        places=cfg.place,
        origin=f"{ctx.config.settings.lat},{ctx.config.settings.long}",
        tomtom_key=ctx.config.secrets[s.tomtom_secret],
        aerodatabox_key=ctx.config.secrets[s.aerodatabox_secret],
    )
    sqlite.init_db(ctx.api.connection)
    plugins.set_runtime(runtime)
