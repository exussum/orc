import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from orc import config
from orc import model as m
from orc.dal.scheduler import Scheduler
from orc.decorators import requires_ctx

RAN: list[str] = []


def test_a_held_queue_defers_until_release_and_then_runs_in_slot_order(tmp_path):
    RAN.clear()
    scheduler = Scheduler(f"sqlite:///{tmp_path / 'jobs.sqlite'}", config.settings.tz)
    scheduler.start(MagicMock())
    scheduler.hold(m.Queue.IOT)
    now = datetime.now(tz=config.settings.tz)
    scheduler.once(_record, now - timedelta(seconds=2), "first", queue=m.Queue.IOT)
    scheduler.once(_record, now - timedelta(seconds=1), "second", queue=m.Queue.IOT)
    scheduler.once(_record, now, "plain")
    scheduler.resume()

    assert _ran(1) == ["plain"]
    assert [deferred.args[0] for deferred in scheduler._executor.deferred] == ["first", "second"]

    scheduler.release(m.Queue.IOT)

    assert _ran(3) == ["plain", "first", "second"]


@requires_ctx
def _record(label: str, *, ctx) -> None:
    RAN.append(label)


def _ran(count: int) -> list[str]:
    deadline = time.monotonic() + 5
    while len(RAN) < count and time.monotonic() < deadline:
        time.sleep(0.01)
    return list(RAN)
