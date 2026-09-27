import pytest

from orc import api
from orc import model as m


@pytest.mark.plugins()
def test_a_tag_heard_for_the_first_time_is_logged_once(house):
    house.advertise("Rex")
    house.tick(seconds=30)
    house.advertise("Rex")

    assert house.log() == [(m.Query("ble"), "Presence detected: `Rex`", [])]
    assert api.present_names() == {"Rex"}


@pytest.mark.plugins()
def test_manual_rescan_clears_and_refinds_everyone(house):
    api._presence_cron_job(ctx=house.ctx)
    house.advertise("Rex")
    house.tick(minutes=1)

    house.press("/api/presence/run")

    assert house.log()[-1] == (
        m.Manual("presence"),
        "Presence rescan",
        ["Presence lost: `Alice, Rex`", "Presence detected: `Rex`", "Presence detected: `Alice`"],
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
        (m.Manual("presence"), "Presence rescan", ["Presence lost: `Alice`", "Presence detected: `Alice`"]),
        (m.Manual("presence"), "Presence rescan", ["Presence lost: `Alice`", "Presence detected: `Alice`"]),
    ]


@pytest.mark.plugins()
def test_the_cron_check_does_not_probe_tags(house):
    house.lan.clear()
    house.advertise("Rex")
    house.tick(hours=1)

    api._presence_cron_job(ctx=house.ctx)

    assert house.probed == []
    assert house.log() == [(m.Query("ble"), "Presence detected: `Rex`", [])]
    assert api.present_names() == {"Rex"}


@pytest.mark.plugins()
def test_a_tag_unheard_for_the_window_is_probed_before_it_is_lost(house):
    house.lan.clear()
    house.advertise("Rex")
    house.tick(hours=10)

    api._presence_cron_job(ctx=house.ctx)

    assert house.probed == ["Rex-tag"]
    assert house.log() == [(m.Query("ble"), "Presence detected: `Rex`", [])]
    assert api.present_names() == {"Rex"}


@pytest.mark.plugins()
def test_a_tag_unheard_and_unreachable_is_lost_at_the_next_check(house):
    house.lan.clear()
    house.advertise("Rex")
    house.tick(hours=10)
    house.leave("Rex")

    api._presence_cron_job(ctx=house.ctx)
    assert house.log()[-1] == (m.Cron("presence"), "Presence lost: `Rex`", [])
    assert api.present_names() == set()

    house.advertise("Rex")
    assert house.log()[-1] == (m.Query("ble"), "Presence detected: `Rex`", [])
