"""LG WebOS TV integration.

Registers the LGTV/WebOS device types, a dispatch handler (on/off via WebOS, with a
BroadLink IR toggle to power on), a "TV" state row set, the pairing action's on-click
notification, a boot hook to create its DB table, and marks WebOS reset-excluded.
"""

from typing import Any

from orc_plugins.lgtv import webos

import orc
from orc import model as m

# orc.LGTV/WebOS/BroadLink are built at runtime from the registered device types; read
# them through an Any view since mypy can't see the dynamic package attributes.
_orc: Any = orc

# Client behavior for the "Pair LG TV" button: show a browser notification while pairing.
PAIRING_JS = """
const dismiss = await notifyPairing(parseFloat(el.dataset.duration));
const q = el.dataset.device ? `?device=${encodeURIComponent(el.dataset.device)}` : "";
await get(`/api/run/Pair LG TV${q}`, el);
dismiss?.();
"""


def _dispatch(w: "m.DeviceEnum", rule: "m.Config", stream: dict[Any, tuple[str, str]]) -> None:
    from orc import api  # deferred: this package is imported during orc's config load

    webos_device, bl_device = _orc.WebOS[w.name], _orc.BroadLink[w.name]
    if rule.state == m.OFF:
        webos.off(webos_device)
    elif rule.state == m.ON:
        if webos.is_off(webos_device):
            api.tv_toggle(bl_device)
    else:
        raise Exception(f"LGTV only supports on and off, got: {rule.state!r}")


def tv_state() -> list[dict[str, Any]]:
    # ``action`` makes each row a clickable runner -> /api/run/Pair LG TV?device=<name>.
    return [{"name": w.name, "action": "Pair LG TV", "state": "off" if webos.is_off(_orc.WebOS[w.name]) else "on"} for w in _orc.LGTV]


def setup(ctx: Any) -> None:
    webos.init_db()
    ctx.api.add_state_provider("TV", tv_state)


def declare(declarations: Any) -> None:
    declarations.declare(
        device_types=["LGTV", "WebOS"],
        controllable=["LGTV"],
        reset_excluded=["WebOS"],
        icons={"LGTV": "tv"},
        dispatch={"LGTV": _dispatch},
        setup=[setup],
        on_click={"Pair LG TV": PAIRING_JS},
        button_labels={"Pair LG TV": "Pair {device}"},
    )
