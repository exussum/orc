import tracemalloc
from typing import Any

from flask import Blueprint, Response

bp = Blueprint("memleak", __name__)
_baseline: list[tracemalloc.Snapshot] = []


def declare(declarations: Any) -> None:
    if not tracemalloc.is_tracing():
        tracemalloc.start(10)
    declarations.declare(blueprints={"memory": bp})


@bp.route("/")
def memory() -> Response:
    snapshot = tracemalloc.take_snapshot().filter_traces((tracemalloc.Filter(False, tracemalloc.__file__),))
    if not _baseline:
        _baseline.append(snapshot)
    stats = snapshot.compare_to(_baseline[0], "lineno")[:25]
    return Response("\n".join(map(str, stats)), mimetype="text/plain")
