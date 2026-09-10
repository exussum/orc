from typing import TYPE_CHECKING, cast

from flask import Blueprint, current_app

if TYPE_CHECKING:
    from orc.view import OrcFlask

example_bp = Blueprint("example", __name__)
app = cast("OrcFlask", current_app)


@example_bp.route("/", methods=["GET"])
def index() -> dict:
    raise NotImplementedError


@example_bp.route("/", methods=["POST"])
def create() -> tuple[dict, int]:
    raise NotImplementedError


@example_bp.route("/<jid>", methods=["DELETE"])
def delete(jid: str) -> tuple[dict | str, int]:
    raise NotImplementedError
