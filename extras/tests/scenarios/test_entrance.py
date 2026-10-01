import pytest
from orc_extras import entrance_sensor

import orc


@pytest.mark.plugins(entrance_sensor)
def test_walk_in_rolls_up_under_the_entrance_entry(house):
    house.entrance_sensor("active")
    house.tick(seconds=30)
    house.entrance_sensor("inactive")
    house.tick(minutes=2)

    entrance = house.broker(orc.Sensor.ENTRANCE_SENSOR)
    assert house.log() == [
        (
            entrance,
            "Entrance sensor triggered",
            [
                "Applying `Day` rules",
                "Entrance sensor triggered",
                "Motion cleared, running `All Lights Off`, cleanup in 2 minutes",
                "`All Lights Off`",
                "Presence detected: `Alice`",
                "`Silence`",
                "Trigger sensor off: skip (people present)",
            ],
        )
    ]


@pytest.mark.plugins(entrance_sensor)
def test_a_second_walk_in_starts_its_own_entry(house):
    for _ in range(2):
        house.entrance_sensor("active")
        house.tick(seconds=30)
        house.entrance_sensor("inactive")
        house.tick(minutes=2)
        house.tick(minutes=1)

    assert [action for _, action, _ in house.log()] == ["Entrance sensor triggered", "Entrance sensor triggered"]


@pytest.mark.plugins(entrance_sensor)
def test_a_broadcast_during_the_pause_is_ignored(house):
    house.lan.clear()
    house.advertise("Rex")
    house.tick(hours=1)
    house.entrance_sensor("active")
    house.tick(seconds=30)
    house.entrance_sensor("inactive")
    house.tick(seconds=20)
    house.advertise("Rex")
    house.tick(minutes=2)

    assert house.log()[-1] == (
        house.broker(orc.Sensor.ENTRANCE_SENSOR),
        "Entrance sensor triggered",
        [
            "Applying `Day` rules",
            "Entrance sensor triggered",
            "Motion cleared, running `All Lights Off`, cleanup in 2 minutes",
            "`All Lights Off`",
            "Queued: `Dog` (until 04:09)",
            "Trigger sensor off: skip (listener home)",
        ],
    )
    assert house.pushed == []


@pytest.mark.plugins(entrance_sensor)
def test_a_listener_who_left_before_the_door_is_lost_at_cleanup(house):
    house.lan.clear()
    house.advertise("Rex")
    house.tick(hours=1)
    house.leave("Rex")
    house.entrance_sensor("active")
    house.tick(seconds=30)
    house.entrance_sensor("inactive")
    house.tick(minutes=2)

    assert house.log()[-1] == (
        house.broker(orc.Sensor.ENTRANCE_SENSOR),
        "Entrance sensor triggered",
        [
            "Applying `Day` rules",
            "Entrance sensor triggered",
            "Motion cleared, running `All Lights Off`, cleanup in 2 minutes",
            "`All Lights Off`",
            "Presence lost: `Rex`",
            "Snapshot for `entrance_sensor` until 04:47: all off",
            "Trigger sensor off: applying OFF",
        ],
    )
    assert house.pushed == []


@pytest.mark.plugins(entrance_sensor)
def test_a_walk_in_with_nobody_tracked_before_or_after_is_pushed(house):
    house.lan.clear()
    house.entrance_sensor("active")
    house.tick(seconds=30)
    house.entrance_sensor("inactive")
    house.tick(minutes=2)

    assert house.log()[-1][2][-2:] == ["Trigger sensor off: applying OFF", "Entrance motion with nobody tracked before or after"]
    assert house.pushed == ["Entrance motion with nobody tracked before or after"]


@pytest.mark.plugins(entrance_sensor)
def test_the_probe_finds_a_quiet_tag(house):
    house.lan.clear()
    house.advertise("Rex")
    house.tick(hours=1)
    house.entrance_sensor("active")
    house.tick(seconds=30)
    house.entrance_sensor("inactive")
    house.tick(minutes=2)

    assert house.log()[-1] == (
        house.broker(orc.Sensor.ENTRANCE_SENSOR),
        "Entrance sensor triggered",
        [
            "Applying `Day` rules",
            "Entrance sensor triggered",
            "Motion cleared, running `All Lights Off`, cleanup in 2 minutes",
            "`All Lights Off`",
            "Queued: `Dog` (until 04:09)",
            "Trigger sensor off: skip (listener home)",
        ],
    )


@pytest.mark.plugins(entrance_sensor)
def test_a_tag_passing_through_mid_visit_counts_for_neither(house):
    house.lan.clear()
    house.entrance_sensor("active")
    house.tick(seconds=30)
    house.entrance_sensor("inactive")
    house.tick(seconds=20)
    house.advertise("Rex")
    house.tick(seconds=20)
    house.leave("Rex")
    house.entrance_sensor("active")
    house.tick(seconds=30)
    house.entrance_sensor("inactive")
    house.tick(minutes=2)

    entrance = house.broker(orc.Sensor.ENTRANCE_SENSOR)
    assert house.log() == [
        (
            entrance,
            "Entrance sensor triggered",
            [
                "Applying `Day` rules",
                "Entrance sensor triggered",
                "Motion cleared, running `All Lights Off`, cleanup in 2 minutes",
                "`All Lights Off`",
                "Cleanup cancelled: motion triggered",
            ],
        ),
        (
            entrance,
            "Entrance sensor triggered",
            [
                "Applying `Day` rules",
                "Entrance sensor triggered",
                "Motion cleared, running `All Lights Off`, cleanup in 2 minutes",
                "`All Lights Off`",
                "Snapshot for `entrance_sensor` until 03:48: all off",
                "Trigger sensor off: applying OFF",
                "Entrance motion with nobody tracked before or after",
            ],
        ),
    ]


@pytest.mark.plugins(entrance_sensor)
def test_a_tag_heard_in_the_quiet_minutes_waits_for_its_next_broadcast(house):
    house.lan.clear()
    house.advertise("Rex")
    house.tick(hours=1)
    assert house.ctx.api.present_names() == {"Rex"}
    house.entrance_sensor("active")
    house.tick(seconds=30)
    house.entrance_sensor("inactive")
    house.tick(seconds=20)
    house.advertise("Rex")
    house.leave("Rex")
    assert house.ctx.api.present_names() == set()
    house.tick(minutes=2)
    assert house.ctx.api.present_names() == set()
    house.advertise("Rex")
    assert house.ctx.api.present_names() == {"Rex"}


@pytest.mark.plugins(entrance_sensor)
def test_a_second_motion_keeps_the_before_set(house):
    house.lan.clear()
    house.advertise("Rex")
    house.tick(hours=1)
    house.entrance_sensor("active")
    house.tick(seconds=30)
    house.entrance_sensor("inactive")
    house.tick(seconds=20)
    house.leave("Rex")
    house.entrance_sensor("active")
    house.tick(seconds=30)
    house.entrance_sensor("inactive")
    house.tick(minutes=2)

    assert house.log()[-1][2][-1] == "Trigger sensor off: applying OFF"
    assert house.pushed == []
