from pathlib import Path
from typing import Any

from command_cfg import array, group, scalar

import example
from example import plugins
from example.dal import sqlite
from example.model import Runtime, Settings, Widget, Zone
from example.web import example_bp
from orc.kernel import cast
from orc.kernel.loader import load_plugin_config
from orc.model import AppContext

CONFIG = "example"
GRAMMAR = """
setting <key> <value>
widget <name> <value>
zone <group> <name> <value>
"""


_SERIALIZERS = {
    "setting": scalar(Settings, types={"window_hours": cast.int, "http_timeout": cast.int}),
    "widget": array(Widget, types={"value": int}),
    "zone": group(Zone),
}


def declare(declarations: Any) -> None:
    s = load_plugin_config(CONFIG, declarations, GRAMMAR, _SERIALIZERS).setting
    declarations.declare(
        controllable=["Example"],
        icons={"Example": "beaker"},
        dispatch={"Example": plugins._dispatch},
        state_providers={"Example Status": plugins.status},
        setup=[setup],
        scripts=[Path(__file__).parent / "static" / "example.js"],
        button_labels={"Example Action": "Run {device}"},
        blueprints={"things": example_bp},
        secrets={s.foo_secret: cast.nonblank, s.bar_secret: cast.nonblank},
    )


def setup(ctx: AppContext) -> None:
    cfg = load_plugin_config(CONFIG, ctx.config, GRAMMAR, _SERIALIZERS)
    s = cfg.setting
    runtime = Runtime(
        foo=cast.module(s.foo_backend),
        bar=cast.module(s.bar_backend),
        settings=s,
        widgets=cfg.widget,
        zones=[z for zs in cfg.zone.values() for z in zs],
        foo_key=ctx.config.secrets.other[s.foo_secret],
        bar_key=ctx.config.secrets.other[s.bar_secret],
    )
    sqlite.init_db(ctx.api.connection)
    ctx.plugin_state[example] = runtime
