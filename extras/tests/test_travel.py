import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests
from orc_extras import travel
from orc_extras.travel import model as m
from orc_extras.travel import plugins
from orc_extras.travel.dal import sqlite as travel_sqlite
from orc_extras.travel.dal.drive import stub as drive_stub
from orc_extras.travel.dal.drive import tomtom
from orc_extras.travel.dal.flight import stub as flight_stub

from orc.model import column_to_value

FIXTURE = Path(__file__).parent / "fixture"
ARRIVE = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


def _setup_runtime():
    ctx = MagicMock()
    ctx.config.plugin_configs = {travel.CONFIG: (FIXTURE / "travel.orc").read_text()}
    travel.setup(ctx)
    return plugins._runtime


def _runtime(extras, buffer=0):
    return m.Runtime(
        drive=drive_stub,
        flight=flight_stub,
        settings=m.Settings("d", "f", "0 6 * * *", 6, "T", "A", 120, buffer),
        extras=extras,
        places=[],
        origin="40.7,-74.0",
        tomtom_key="",
        aerodatabox_key="",
    )


def test_travel_config_loads():
    rt = _setup_runtime()
    assert rt.settings == m.Settings(
        drive_backend="orc_extras.travel.dal.drive.stub",
        flight_backend="orc_extras.travel.dal.flight.stub",
        cron="0 6 * * *",
        window_hours=6,
        tomtom_secret="TOMTOM_KEY",
        aerodatabox_secret="AERODATABOX_KEY",
        http_timeout=120,
    )
    assert rt.places == [m.Place("Home", "123 Main St, Springfield"), m.Place("Office", "500 Market St, Metropolis")]
    assert rt.extras == [m.Extra("Coffee", 10), m.Extra("Parking", 20)]
    assert rt.drive is drive_stub and rt.flight is flight_stub


@pytest.mark.parametrize(
    "path,func",
    [
        ("orc_extras.travel.dal.drive.tomtom", "drive_minutes"),
        ("orc_extras.travel.dal.drive.stub", "drive_minutes"),
        ("orc_extras.travel.dal.flight.aerodatabox", "arrival"),
        ("orc_extras.travel.dal.flight.stub", "arrival"),
    ],
)
def test_backends_resolve(path, func):
    assert callable(getattr(column_to_value("module", path), func))


def test_drive_minutes_deletes_cached_geocode_on_http_error(monkeypatch):
    conn = sqlite3.connect(":memory:")
    connection = lambda: conn  # noqa: E731
    travel_sqlite.init_db(connection)
    travel_sqlite.insert_geocode(connection, "Bad Dest", 1.0, 2.0)

    class FailingResponse:
        def raise_for_status(self):
            raise requests.HTTPError("400 Client Error")

    monkeypatch.setattr(tomtom.requests, "get", lambda *a, **k: FailingResponse())

    with pytest.raises(requests.HTTPError):
        tomtom.drive_minutes(connection, "key", "origin", "Bad Dest", 5)

    assert travel_sqlite.fetch_geocode(connection, "Bad Dest") is None


@pytest.mark.parametrize(
    "value,expected",
    [
        ("AA657", True),
        ("BAW185", True),
        ("123 Main St, Springfield", False),
        ("11372", False),
        ("Home", False),
    ],
)
def test_is_flight(value, expected):
    assert plugins.is_flight(value) is expected


def test_from_submission_flight():
    job = m.TravelJob.from_submission(m.Submission(destination="JFK", arrive=ARRIVE, flight="AA1", extras=["Coffee"]))
    assert (job.summary, job.iata, job.airport, job.extras) == ("AA1", "AA1", "JFK", {"Coffee"})


def test_from_submission_destination():
    job = m.TravelJob.from_submission(m.Submission(destination="Home", arrive=ARRIVE, flight=None, extras=[]))
    assert (job.summary, job.destination, job.iata) == ("Home", "Home", None)


def test_from_submission_requires_arrive():
    with pytest.raises(ValueError, match="arrive is required"):
        m.TravelJob.from_submission(m.Submission(destination="Home", arrive=None, flight=None, extras=[]))


def test_from_submission_requires_destination_or_flight():
    with pytest.raises(ValueError, match="destination is required"):
        m.TravelJob.from_submission(m.Submission(destination=None, arrive=ARRIVE, flight=None, extras=[]))


def test_arrival_flight_uses_backend():
    job = m.TravelJob("AA1", "", ARRIVE, set(), iata="AA1", airport="JFK")
    assert plugins._arrival(_runtime([]), job, timezone.utc) == m.Arrival(ARRIVE, "JFK", "1")


def test_arrival_destination():
    job = m.TravelJob("Home", "Home", ARRIVE, set())
    assert plugins._arrival(_runtime([]), job, timezone.utc) == m.Arrival(ARRIVE, "Home", None)


def _boom(msg):
    def raiser(*args, **kwargs):
        raise RuntimeError(msg)

    return raiser


def test_evaluate_destination_success(monkeypatch):
    monkeypatch.setattr(plugins, "_runtime", _runtime([]))
    job = m.TravelJob("Home", "Home", ARRIVE, set())
    arrival, sched = plugins.evaluate(job, timezone.utc, ARRIVE - timedelta(hours=1), lambda: None)
    assert arrival == m.Arrival(ARRIVE, "Home", None)
    assert sched.leave_at is not None


def test_evaluate_destination_failure_bubbles_reason(monkeypatch):
    monkeypatch.setattr(plugins, "_runtime", _runtime([])._replace(drive=SimpleNamespace(drive_minutes=_boom("route unavailable"))))
    job = m.TravelJob("Nowhere", "Nowhere", ARRIVE, set())
    with pytest.raises(ValueError, match="route unavailable"):
        plugins.evaluate(job, timezone.utc, ARRIVE - timedelta(hours=1), lambda: None)


def test_evaluate_flight_same_day_checks_aviation(monkeypatch):
    monkeypatch.setattr(plugins, "_runtime", _runtime([])._replace(flight=SimpleNamespace(arrival=_boom("no such flight"))))
    job = m.TravelJob("AA1", "", ARRIVE, set(), iata="AA1", airport="JFK")
    with pytest.raises(ValueError, match="no such flight"):
        plugins.evaluate(job, timezone.utc, ARRIVE, lambda: None)


def test_evaluate_flight_future_date_skips_check(monkeypatch):
    monkeypatch.setattr(plugins, "_runtime", _runtime([])._replace(flight=SimpleNamespace(arrival=_boom("should not be called"))))
    job = m.TravelJob("AA1", "", ARRIVE, set(), iata="AA1", airport="JFK")
    arrival, sched = plugins.evaluate(job, timezone.utc, ARRIVE - timedelta(days=5), lambda: None)
    assert arrival is None
    assert sched.leave_at is None


def test_next_run_signals_leave_now_within_ten_minutes():
    job = m.TravelJob("Home", "Home", ARRIVE, set())
    now = ARRIVE - timedelta(minutes=35)
    sched = plugins.next_run(_runtime([]), job, now, m.Arrival(ARRIVE, "Home", None), lambda: None)
    assert sched == m.Schedule(ARRIVE - timedelta(minutes=30), None)


def test_next_run_marks_late_and_computes_eta_if_leaving_now():
    job = m.TravelJob("Home", "Home", ARRIVE, set())
    sched = plugins.next_run(_runtime([]), job, ARRIVE, m.Arrival(ARRIVE, "Home", None), lambda: None)
    assert sched.late is True
    assert sched.leave_at == ARRIVE - timedelta(minutes=30)
    assert sched.eta == ARRIVE + timedelta(minutes=30)


def test_next_run_leave_time_includes_selected_extras():
    job = m.TravelJob("Home", "Home", ARRIVE, {"Coffee"})
    sched = plugins.next_run(
        _runtime([m.Extra("Coffee", 10), m.Extra("Parking", 20)]), job, ARRIVE, m.Arrival(ARRIVE, "Home", None), lambda: None
    )
    assert sched.leave_at == ARRIVE - timedelta(minutes=40)


def test_next_run_leave_time_includes_buffer():
    job = m.TravelJob("Home", "Home", ARRIVE, set())
    sched = plugins.next_run(_runtime([], buffer=10), job, ARRIVE, m.Arrival(ARRIVE, "Home", None), lambda: None)
    assert sched.leave_at == ARRIVE - timedelta(minutes=40)


def test_next_run_waits_for_midnight_when_no_arrival():
    job = m.TravelJob("Home", "Home", ARRIVE, set())
    sched = plugins.next_run(_runtime([]), job, ARRIVE - timedelta(days=1), None, lambda: None)
    assert sched == m.Schedule(None, datetime(2026, 1, 1, tzinfo=timezone.utc))


def test_next_run_steps_to_two_hours_before_leave():
    job = m.TravelJob("Home", "Home", ARRIVE, set())
    sched = plugins.next_run(_runtime([]), job, ARRIVE - timedelta(hours=3), m.Arrival(ARRIVE, "Home", None), lambda: None)
    assert sched.next_fire == ARRIVE - timedelta(minutes=30) - timedelta(hours=2)


def test_next_run_rechecks_every_ten_minutes_within_two_hours():
    job = m.TravelJob("Home", "Home", ARRIVE, set())
    now = ARRIVE - timedelta(hours=1)
    sched = plugins.next_run(_runtime([]), job, now, m.Arrival(ARRIVE, "Home", None), lambda: None)
    assert sched.next_fire == now + timedelta(minutes=10)


def test_run_job_syncs_arrive_to_live_verified_time(monkeypatch):
    verified = ARRIVE + timedelta(hours=10)
    rt = _runtime([])._replace(flight=SimpleNamespace(arrival=lambda *a, **k: (verified, "JFK", None)))
    monkeypatch.setattr(plugins, "_runtime", rt)
    monkeypatch.setattr(plugins, "_reschedule", lambda *a, **k: None)
    job = m.TravelJob("AA1", "", ARRIVE, set(), iata="AA1", airport="JFK")
    ctx = MagicMock()
    ctx.config.settings.tz = timezone.utc
    ctx.api.local_now.return_value = ARRIVE - timedelta(hours=3)
    plugins.run_job(job, ctx=ctx)
    assert job.arrive == verified
