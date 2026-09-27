from pathlib import Path
from typing import Any

from command_cfg import array, scalar

import orc_extras.travel
from orc.kernel import cast
from orc.kernel.loader import load_plugin_config
from orc.model import AppContext
from orc_extras.travel.dal import sqlite
from orc_extras.travel.model import Extra, Place, Runtime, Settings
from orc_extras.travel.web import travel_bp

CONFIG = "orc_extras/travel"
GRAMMAR = """
setting <key> <value>
place <name> <address>
extra <name> <minutes>
"""


_SERIALIZERS = {
    "setting": scalar(Settings, types={"window_hours": cast.int, "http_timeout": cast.int, "buffer_minutes": cast.int}),
    "place": array(Place),
    "extra": array(Extra, types={"minutes": int}),
}


def declare(declarations: Any) -> None:
    s = load_plugin_config(CONFIG, declarations, GRAMMAR, _SERIALIZERS).setting
    declarations.declare(
        setup=[setup],
        blueprints={"jobs": travel_bp},
        scripts=[Path(__file__).parent / "static" / "travel.js"],
        secrets={s.tomtom_secret: cast.nonblank, s.aerodatabox_secret: cast.nonblank},
    )


def setup(ctx: AppContext) -> None:
    cfg = load_plugin_config(CONFIG, ctx.config, GRAMMAR, _SERIALIZERS)
    s = cfg.setting
    runtime = Runtime(
        drive=cast.module(s.drive_backend),
        flight=cast.module(s.flight_backend),
        settings=s,
        extras=cfg.extra,
        places=cfg.place,
        origin=f"{ctx.config.settings.lat},{ctx.config.settings.long}",
        tomtom_key=ctx.config.secrets.other[s.tomtom_secret],
        aerodatabox_key=ctx.config.secrets.other[s.aerodatabox_secret],
    )
    sqlite.init_db(ctx.api.connection)
    ctx.plugin_state[orc_extras.travel] = runtime
