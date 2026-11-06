import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from orc import config
from orc import model as m
from orc.dal.scheduler import Scheduler

RAN: list[str] = []


def test_a_held_queue_defers_until_release_and_then_runs_in_slot_order(tmp_path):
    RAN.clear()
    scheduler = Scheduler(f"sqlite:///{tmp_path / 'jobs.sqlite'}", config.settings.tz)
    scheduler.start(MagicMock())
    scheduler.resume()
    scheduler.pause_queue(m.Queue.IOT)
    now = datetime.now(tz=config.settings.tz)
    scheduler.once(_record, now - timedelta(seconds=2), "first", queue=m.Queue.IOT)
    scheduler.once(_record, now - timedelta(seconds=1), "second", queue=m.Queue.IOT)
    scheduler.once(_record, now, "plain")

    assert _ran(1) == ["plain"]
    iot = next(q for q in scheduler.stores() if q.name == "iot")
    assert iot.paused_since and [j.payload for j in iot.jobs] == ["first", "second"]

    scheduler.release_queue(m.Queue.IOT)

    assert _ran(3) == ["plain", "first", "second"]


def _record(payload: str, *, ctx) -> None:
    RAN.append(payload)


def _ran(count: int) -> list[str]:
    deadline = time.monotonic() + 5
    while len(RAN) < count and time.monotonic() < deadline:
        time.sleep(0.01)
    return list(RAN)
