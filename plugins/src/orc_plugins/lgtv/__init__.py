"""LG WebOS TV integration.

Registers the LGTV/WebOS device types, a dispatch handler (on/off via WebOS, with a
BroadLink IR toggle to power on), a "TV" state row set, the pairing button's browser
plugin (static/lgtv.js), and a boot hook to create its DB table.
"""

from pathlib import Path
from typing import Any

from orc_plugins.lgtv import plugins

import orc
from orc import model as m

# orc.LGTV/WebOS/BroadLink are built at runtime from the registered device types; read
# them through an Any view since mypy can't see the dynamic package attributes.
_orc: Any = orc


def setup(ctx: "m.AppContext") -> None:
    plugins.init_db(ctx.api.connection)


def _dispatch(ctx: "m.AppContext", w: "m.DeviceEnum", rule: "m.Config", stream: dict[Any, tuple[str, str]]) -> None:
    webos_device, bl_device = _orc.WebOS[w.name], _orc.BroadLink[w.name]
    if rule.state == m.OFF:
        plugins.off(ctx.api.connection, webos_device)
    elif rule.state == m.ON:
        if plugins.is_off(webos_device):
            ctx.api.tv_toggle(bl_device)
    else:
        raise Exception(f"LGTV only supports on and off, got: {rule.state!r}")


def tv_state() -> list[dict[str, Any]]:
    # ``action`` makes each row a clickable runner -> /api/run/Pair LG TV?device=<name>.
    return [{"name": w.name, "action": "Pair LG TV", "state": "off" if plugins.is_off(_orc.WebOS[w.name]) else "on"} for w in _orc.LGTV]


def declare(declarations: Any) -> None:
    declarations.declare(
        controllable=["LGTV"],
        icons={"LGTV": "tv"},
        dispatch={"LGTV": _dispatch},
        state_providers={"TV": tv_state},
        setup=[setup],
        scripts=[Path(__file__).parent / "static" / "lgtv.js"],
        button_labels={"Pair LG TV": "Pair {device}"},
    )
