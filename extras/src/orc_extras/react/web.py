from typing import TYPE_CHECKING, cast

from flask import Blueprint, abort, current_app

import orc_extras.react
from orc.kernel import engine
from orc.model import Devices
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
            {
                "id": index,
                "name": name,
                "sleeping_until": max((u.isoformat() for rule in rules if (u := state.disabled_until(rule, now))), default=None),
            }
            for index, (name, rules) in enumerate(state.named().items())
        ]
    }


@react_bp.route("/<int:index>/sleep", methods=["POST"])
def sleep(index: int) -> tuple[dict, int]:
    return {"sleeping_until": plugins.sleep(app.orc, *_named(index)).isoformat()}, 201


@react_bp.route("/<int:index>/sleep", methods=["DELETE"])
def wake(index: int) -> tuple[str, int]:
    plugins.wake(app.orc, *_named(index))
    return "", 204


def _named(index: int) -> tuple[str, list[engine.Rule[Devices]]]:
    named = list(app.orc.plugin_state[orc_extras.react].named().items())
    if not 0 <= index < len(named):
        abort(404)
    return named[index]
