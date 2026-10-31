import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from orc import config
from orc.dal.scheduler import Scheduler
from orc.decorators import requires_ctx

RAN: list[str] = []


def test_a_held_type_defers_until_release_and_then_runs_in_slot_order(tmp_path):
    RAN.clear()
    scheduler = Scheduler(f"sqlite:///{tmp_path / 'jobs.sqlite'}", config.settings.tz)
    scheduler.start(MagicMock())
    scheduler.hold(_Marked)
    now = datetime.now(tz=config.settings.tz)
    scheduler.once(_record, now - timedelta(seconds=2), _Marked("first"))
    scheduler.once(_record, now - timedelta(seconds=1), _Marked("second"))
    scheduler.once(_record, now, "plain")
    scheduler.resume()

    assert _ran(1) == ["plain"]
    assert [deferred.args[0].label for deferred in scheduler._executor.deferred] == ["first", "second"]

    scheduler.release(_Marked)

    assert _ran(3) == ["plain", "first", "second"]


class _Marked:
    def __init__(self, label: str) -> None:
        self.label = label


@requires_ctx
def _record(marked, *, ctx) -> None:
    RAN.append(marked if isinstance(marked, str) else marked.label)


def _ran(count: int) -> list[str]:
    deadline = time.monotonic() + 5
    while len(RAN) < count and time.monotonic() < deadline:
        time.sleep(0.01)
    return list(RAN)
