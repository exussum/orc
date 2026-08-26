import sys
from pathlib import Path
from typing import Any

from command_cfg import array, group, scalar
from orc_extras.example import plugins
from orc_extras.example.dal import sqlite
from orc_extras.example.model import Runtime, Settings, Widget, Zone
from orc_extras.example.web import example_bp

from orc.loader import Cast, load_plugin_config
from orc.model import AppContext

CONFIG = "orc_extras/example"
GRAMMAR = """
setting <key> <value>
widget <name> <value>
zone <group> <name> <value>
"""

_SETTING_TYPES = {"window_hours": int, "http_timeout": int}
_WIDGET_TYPES = {"value": int}


def declare(declarations: Any) -> None:
    declarations.declare(
        controllable=["Example"],
        icons={"Example": "beaker"},
        dispatch={"Example": plugins._dispatch},
        state_providers={"Example Status": plugins.status},
        setup=[setup],
        scripts=[Path(__file__).parent / "static" / "example.js"],
        button_labels={"Example Action": "Run {device}"},
        blueprints={"things": example_bp},
    )


def setup(ctx: AppContext) -> None:
    try:
        cfg = load_plugin_config(
            CONFIG,
            ctx.config.plugin_configs,
            GRAMMAR,
            {"setting": scalar(Settings, types=_SETTING_TYPES), "widget": array(Widget, types=_WIDGET_TYPES), "zone": group(Zone)},
        )
        s = cfg.setting
        runtime = Runtime(
            foo=Cast.module(s.foo_backend),
            bar=Cast.module(s.bar_backend),
            settings=s,
            widgets=cfg.widget,
            zones=[z for zs in cfg.zone.values() for z in zs],
            foo_key=ctx.config.secrets[s.foo_secret],
            bar_key=ctx.config.secrets[s.bar_secret],
        )
    except Exception as exc:
        print(f"Failed to load plugin config {CONFIG!r}: {exc}", file=sys.stderr)
        return
    sqlite.init_db(ctx.api.connection)
    plugins.set_runtime(runtime)
