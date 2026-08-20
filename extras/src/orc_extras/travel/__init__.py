import sys
from pathlib import Path
from typing import Any

from command_cfg import array, scalar
from orc_extras.travel import plugins
from orc_extras.travel.dal import sqlite
from orc_extras.travel.model import Extra, Place, Runtime, Settings
from orc_extras.travel.web import travel_bp

from orc import model as m
from orc.loader import load_plugin_config
from orc.model import AppContext

CONFIG = "orc_extras/travel"
GRAMMAR = """
setting <key> <value>
place <name> <address>
extra <name> <minutes>
"""


def declare(declarations: Any) -> None:
    declarations.declare(setup=[setup], blueprints={"jobs": travel_bp}, scripts=[Path(__file__).parent / "static" / "travel.js"])


def setup(ctx: AppContext) -> None:
    try:
        cfg = load_plugin_config(
            CONFIG,
            ctx.config.plugin_configs,
            GRAMMAR,
            {"setting": scalar(Settings), "place": array(Place), "extra": array(Extra)},
        )
        s = cfg.setting
        runtime = Runtime(
            drive=m.column_to_value("module", s.drive_backend),
            flight=m.column_to_value("module", s.flight_backend),
            settings=s,
            extras=[Extra(e.name, int(e.minutes)) for e in cfg.extra],
            places=cfg.place,
            origin=f"{ctx.config.settings.lat},{ctx.config.settings.long}",
            tomtom_key=ctx.config.secrets[s.tomtom_secret],
            aerodatabox_key=ctx.config.secrets[s.aerodatabox_secret],
        )
    except Exception as exc:
        print(f"Failed to load plugin config {CONFIG!r}: {exc}", file=sys.stderr)
        return
    sqlite.init_db(ctx.api.connection)
    plugins.set_runtime(runtime)
