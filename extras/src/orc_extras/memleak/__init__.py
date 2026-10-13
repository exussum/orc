import linecache
import tracemalloc
from collections import deque
from datetime import datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, cast

import psutil
from command_cfg import scalar
from flask import Blueprint, current_app
from orc_engine import cast as engine_cast

import orc_extras.memleak
from orc.kernel.loader import load_plugin_config
from orc.model import AppContext
from orc.plugins import requires_ctx

if TYPE_CHECKING:
    from orc.view import OrcFlask

CONFIG = "orc_extras/memleak"
CRON_ID = "memleak-cron"
GRAMMAR = """
setting <key> <value>
"""
HISTORY = 48
IMPORT_MACHINERY = ("importlib", "<frozen abc>", "/abc.py", "/enum.py", "/dataclasses.py", "/re/_compiler.py", "/typing.py")
ROWS = 25

bp = Blueprint("memleak", __name__)
app = cast("OrcFlask", current_app)


class Row(NamedTuple):
    location: str
    kb: float
    kb_per_hour: float
    grown: int
    intervals: int
    since_boot_kb: float


class Sample(NamedTuple):
    taken: datetime
    sizes: dict[str, int]
    rss: int
    heap: int
    tracer: int


class Settings(NamedTuple):
    threshold_kb: float
    snapshot_minute: int


class State(NamedTuple):
    settings: Settings
    history: deque[Sample]


_SERIALIZERS = {"setting": scalar(Settings, types={"threshold_kb": engine_cast.float, "snapshot_minute": engine_cast.int})}


def declare(declarations: Any) -> None:
    if not tracemalloc.is_tracing():
        tracemalloc.start()
    declarations.declare(setup=[setup], blueprints={"memory": bp}, scripts=[Path(__file__).parent / "static" / "memleak.js"])


def setup(ctx: AppContext) -> None:
    settings = load_plugin_config(CONFIG, ctx.config, GRAMMAR, _SERIALIZERS).setting
    state = State(settings, deque([sample(ctx.api.local_now())], maxlen=HISTORY))
    ctx.plugin_state[orc_extras.memleak] = state
    ctx.scheduler.on_rebuild(lambda: _add_cron(ctx, settings))
    _add_cron(ctx, settings)


@bp.route("/")
def memory() -> dict[str, Any]:
    now = app.orc.api.local_now()
    state = app.orc.plugin_state[orc_extras.memleak]
    history = list(state.history)
    return {
        "since": history[0].taken.isoformat(),
        "samples": len(history),
        "threshold_kb": state.settings.threshold_kb,
        "next_snapshot": _next_snapshot(now, state.settings.snapshot_minute).isoformat(),
        "totals": {name: _total(history, name) for name in ("rss", "heap", "tracer")},
        "rows": [row._asdict() for row in rank(history, state.settings.threshold_kb)],
    }


def sample(now: datetime) -> Sample:
    rss = psutil.Process().memory_info().rss
    heap = tracemalloc.get_traced_memory()[0]
    tracer = tracemalloc.get_tracemalloc_memory()
    sizes: dict[str, int] = {}
    snapshot = tracemalloc.take_snapshot().filter_traces(
        [tracemalloc.Filter(False, f) for f in (__file__, tracemalloc.__file__, linecache.__file__)]
    )
    for stat in snapshot.statistics("lineno"):
        key = _location(stat.traceback[0])
        sizes[key] = sizes.get(key, 0) + stat.size
    return Sample(now, sizes, rss, heap, tracer)


def rank(history: list[Sample], threshold_kb: float) -> list[Row]:
    first, last = history[0], history[-1]
    hours = max((last.taken - first.taken).total_seconds() / 3600, 1)
    rows = []
    for key, size in last.sizes.items():
        since_boot = size - first.sizes.get(key, 0)
        if since_boot < threshold_kb * 1024:
            continue
        grown = sum(1 for a, b in pairwise(history) if b.sizes.get(key, 0) > a.sizes.get(key, 0))
        rows.append(Row(key, size / 1024, since_boot / 1024 / hours, grown, len(history) - 1, since_boot / 1024))
    rows.sort(key=lambda r: (r.grown, r.kb_per_hour), reverse=True)
    return rows[:ROWS]


def _add_cron(ctx: AppContext, settings: Settings) -> None:
    ctx.scheduler.cron(_snapshot, f"{settings.snapshot_minute} * * * *", id=CRON_ID, name="Memory Cron")


def _total(history: list[Sample], name: str) -> dict[str, float]:
    first, last = getattr(history[0], name), getattr(history[-1], name)
    return {"mb": last / 1024 / 1024, "since_boot_mb": (last - first) / 1024 / 1024}


def _next_snapshot(now: datetime, minute: int) -> datetime:
    at = now.replace(minute=minute, second=0, microsecond=0)
    if at <= now:
        at += timedelta(hours=1)
    return at


@requires_ctx
def _snapshot(*, ctx: AppContext) -> None:
    ctx.plugin_state[orc_extras.memleak].history.append(sample(ctx.api.local_now()))


def _location(frame: tracemalloc.Frame) -> str:
    filename = frame.filename
    if any(part in filename for part in IMPORT_MACHINERY):
        return "import machinery"
    for marker in ("site-packages/", "lib/python3.14/"):
        if marker in filename:
            filename = filename.split(marker, 1)[1]
    return f"{filename}:{frame.lineno}"
