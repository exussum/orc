import pytest

from orc import api
from orc import model as m
from orc.interfaces import inbound


@pytest.mark.plugins()
def test_a_tag_heard_for_the_first_time_is_logged_once(house):
    house.advertise("Rex")
    house.tick(seconds=30)
    house.advertise("Rex")

    assert house.log() == [(house.like(m.Query("ble", log_source=m.LogSource.SYSTEM)), "Presence detected: `Rex`", [])]
    assert api.present_names() == {"Rex"}


@pytest.mark.plugins()
def test_manual_rescan_clears_and_refinds_everyone(house):
    house.scan()
    house.advertise("Rex")
    house.tick(minutes=1)

    house.press("/api/presence/run")

    assert house.log()[-1] == (
        house.like(m.Manual("presence", log_source=m.LogSource.MANUAL)),
        "Presence rescan",
        ["Presence lost: `Alice`", "Presence detected: `Alice`"],
    )
    assert api.present_names() == {"Alice", "Rex"}


@pytest.mark.plugins()
def test_two_rescans_an_hour_apart_are_two_rows(house):
    house.advertise("Alice")
    house.tick(minutes=1)

    house.press("/api/presence/run")
    house.tick(hours=1)
    house.press("/api/presence/run")

    assert house.log()[-2:] == [
        (house.like(m.Manual("presence", log_source=m.LogSource.MANUAL)), "Presence rescan", []),
        (house.like(m.Manual("presence", log_source=m.LogSource.MANUAL)), "Presence rescan", []),
    ]


@pytest.mark.plugins()
def test_the_cron_check_does_not_probe_tags(house):
    house.lan.clear()
    house.advertise("Rex")
    house.tick(hours=1)

    inbound.check_presence_cron()

    assert house.probed == []
    assert house.log() == [(house.like(m.Query("ble", log_source=m.LogSource.SYSTEM)), "Presence detected: `Rex`", [])]
    assert api.present_names() == {"Rex"}


@pytest.mark.plugins()
def test_a_tag_unheard_for_the_window_is_probed_before_it_is_lost(house):
    house.lan.clear()
    house.advertise("Rex")
    house.tick(hours=10)

    inbound.check_presence_cron()

    assert house.probed == ["Rex"]
    assert house.log() == [(house.like(m.Query("ble", log_source=m.LogSource.SYSTEM)), "Presence detected: `Rex`", [])]
    assert api.present_names() == {"Rex"}


@pytest.mark.plugins()
def test_a_tag_unheard_and_unreachable_is_lost_at_the_next_check(house):
    house.lan.clear()
    house.advertise("Rex")
    house.tick(hours=10)
    house.leave("Rex")

    inbound.check_presence_cron()
    assert house.log()[-1] == (house.like(m.Cron("presence", log_source=m.LogSource.SYSTEM)), "Presence lost: `Rex`", [])
    assert api.present_names() == set()

    house.advertise("Rex")
    assert house.log()[-1] == (house.like(m.Query("ble", log_source=m.LogSource.SYSTEM)), "Presence detected: `Rex`", [])
