from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from command_cfg import array, scalar
from orc_plugins import travel
from orc_plugins.travel import plugins
from orc_plugins.travel.dal.drive import stub as drive_stub
from orc_plugins.travel.dal.flight import stub as flight_stub
from orc_plugins.travel.model import (
    Extra,
    Place,
    Plan,
    Runtime,
    Settings,
    Submission,
    TravelJob,
)

from orc.loader import load_plugin_config
from orc.model import column_to_value

FIXTURE = Path(__file__).parent / "fixture"
ARRIVE = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


def _load():
    return load_plugin_config(
        travel.CONFIG,
        {travel.CONFIG: (FIXTURE / "travel.orc").read_text()},
        travel.GRAMMAR,
        serializers={"setting": scalar(Settings), "place": array(Place), "extra": array(Extra)},
    )


def _runtime(extras):
    return Runtime(
        drive=drive_stub,
        flight=flight_stub,
        settings=Settings("d", "f", "0 6 * * *", 6, "T", "A", 120),
        extras=extras,
        places=[],
        origin="123 Main St",
        tomtom_key="",
        aerodatabox_key="",
    )


def test_travel_config_loads():
    config = _load()
    assert config.setting == Settings(
        drive_backend="orc_plugins.travel.dal.drive.stub",
        flight_backend="orc_plugins.travel.dal.flight.stub",
        cron="0 6 * * *",
        window_hours=6,
        tomtom_secret="TOMTOM_KEY",
        aerodatabox_secret="AERODATABOX_KEY",
        http_timeout=120,
    )
    assert config.place == [Place("Home", "123 Main St, Springfield"), Place("Office", "500 Market St, Metropolis")]
    assert config.extra == [Extra("Coffee", "10"), Extra("Parking", "20")]


@pytest.mark.parametrize(
    "path,func",
    [
        ("orc_plugins.travel.dal.drive.tomtom", "drive_minutes"),
        ("orc_plugins.travel.dal.drive.stub", "drive_minutes"),
        ("orc_plugins.travel.dal.flight.aerodatabox", "arrival"),
        ("orc_plugins.travel.dal.flight.stub", "arrival"),
    ],
)
def test_backends_resolve(path, func):
    assert callable(getattr(column_to_value("module", path), func))


def test_from_submission_flight():
    job = TravelJob.from_submission(Submission(destination="JFK", arrive=ARRIVE, flight="AA1", extras=["Coffee"]))
    assert (job.summary, job.iata, job.airport, job.extras) == ("AA1", "AA1", "JFK", {"Coffee"})


def test_from_submission_destination():
    job = TravelJob.from_submission(Submission(destination="Home", arrive=ARRIVE, flight=None, extras=[]))
    assert (job.summary, job.destination, job.iata) == ("Home", "Home", None)


def test_from_submission_requires_arrive():
    with pytest.raises(ValueError, match="arrive is required"):
        TravelJob.from_submission(Submission(destination="Home", arrive=None, flight=None, extras=[]))


def test_from_submission_requires_destination_or_flight():
    with pytest.raises(ValueError, match="destination is required"):
        TravelJob.from_submission(Submission(destination=None, arrive=ARRIVE, flight=None, extras=[]))


def test_leave_by_destination_subtracts_drive_time():
    job = TravelJob("Home", "Home", ARRIVE, set())
    plan = plugins.leave_by(_runtime([]), job, timezone.utc, lambda: None)
    assert plan == Plan(ARRIVE - timedelta(minutes=30), "Home", None)


def test_leave_by_adds_selected_extras():
    job = TravelJob("Home", "Home", ARRIVE, {"Coffee"})
    plan = plugins.leave_by(_runtime([Extra("Coffee", 10), Extra("Parking", 20)]), job, timezone.utc, lambda: None)
    assert plan.leave_at == ARRIVE - timedelta(minutes=40)


def test_leave_by_flight_uses_arrival_backend():
    job = TravelJob("AA1", "", ARRIVE, set(), iata="AA1", airport="JFK")
    plan = plugins.leave_by(_runtime([]), job, timezone.utc, lambda: None)
    assert (plan.where, plan.terminal, plan.leave_at) == ("JFK", "1", ARRIVE - timedelta(minutes=30))
