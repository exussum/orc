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
from orc_extras.lg_tv.dal.interfaces import WebOsBackend
from orc_extras.lg_tv.plugins import pair_tv  # noqa: F401


def setup(ctx: "m.AppContext") -> None:
    sqlite.init_db(ctx.api.connection)
    ctx.api.add_state_provider("TV", partial(tv_state, ctx, plugins.backend(ctx)))


def _dispatch(ctx: "m.AppContext", w: "m.DeviceEnum", rule: "m.Config", stream: dict[Any, tuple[str, str]]) -> None:
    webos_device, bl_device = ctx.orc.WebOS[w.name], ctx.orc.BroadLink[w.name]
    if rule.state == m.OFF:
        plugins.off(ctx, webos_device)
    elif rule.state == m.ON:
        if plugins.is_off(ctx, webos_device):
            ctx.api.tv_toggle(bl_device)
    else:
        raise Exception(f"LGTV only supports on and off, got: {rule.state!r}")


def tv_state(ctx: "m.AppContext", backend: "WebOsBackend") -> list[m.DeviceStatus]:
    # ``action`` makes each row a clickable runner -> /api/run/Pair LG TV?device=<name>.
    return [
        m.DeviceStatus(
            name=w.name,
            label=w.label,
            action="Pair LG TV",
            details={"state": "off" if backend.is_off(ctx.orc.WebOS[w.name]) else "on"},
        )
        for w in ctx.orc.LGTV
    ]


def declare(declarations: Any) -> None:
    declarations.declare(
        controllable=["LGTV"],
        icons={"LGTV": "tv"},
        dispatch={"LGTV": _dispatch},
        setup=[setup],
        scripts=[Path(__file__).parent / "static" / "lg_tv.js"],
        button_labels={"Pair LG TV": "Pair {device}"},
    )
