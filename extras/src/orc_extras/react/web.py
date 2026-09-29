from typing import TYPE_CHECKING, cast

from flask import Blueprint, abort, current_app, request

import orc_extras.react
from orc_extras.react import plugins

if TYPE_CHECKING:
    from orc.view import OrcFlask

react_bp = Blueprint("react", __name__)
app = cast("OrcFlask", current_app)


@react_bp.route("/", methods=["GET"])
def rules() -> dict:
    ctx = app.orc
    state = ctx.plugin_state[orc_extras.react]
    now = ctx.api.local_now()
    return {
        "rules": [
            {"name": name, "sleeping_until": until.isoformat() if (until := plugins.disabled_until(state, name, now)) else None}
            for name in state.groups
        ]
    }


@react_bp.route("/<path:name>/sleep")
def sleep(name: str) -> dict:
    if request.args.get("sleeping") == "1":
        return {"sleeping_until": plugins.sleep(app.orc, _known(name)).isoformat()}
    plugins.wake(app.orc, _known(name))
    return {"sleeping_until": None}


def _known(name: str) -> str:
    if name not in app.orc.plugin_state[orc_extras.react].groups:
        abort(404)
    return name
