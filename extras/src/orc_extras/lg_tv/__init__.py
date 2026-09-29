"""LG WebOS TV integration.

Registers the LGTV/WebOS device types, a dispatch handler (on/off via WebOS, with a
BroadLink IR toggle to power on), a "TV" state row set, the pairing button's browser
plugin (static/lg_tv.js), and a boot hook to create its DB table.
"""

from functools import partial
from pathlib import Path
from typing import Any

from orc import model as m
from orc_extras.lg_tv import plugins
from orc_extras.lg_tv.dal import sqlite
from orc_extras.lg_tv.plugins import pair_tv  # noqa: F401


def setup(ctx: "m.AppContext") -> None:
    sqlite.init_db(ctx.api.connection)
    ctx.api.add_state_provider("TV", partial(plugins.tv_state, ctx, plugins.backend(ctx)))


def declare(declarations: Any) -> None:
    declarations.declare(
        controllable=["LGTV"],
        icons={"LGTV": "tv"},
        dispatch={"LGTV": plugins._dispatch},
        setup=[setup],
        scripts=[Path(__file__).parent / "static" / "lg_tv.js"],
        button_labels={"Pair LG TV": "Pair {device}"},
    )
