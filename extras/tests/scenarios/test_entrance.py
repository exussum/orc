from unittest.mock import patch

import pytest
from orc_extras import entrance_sensor

import orc
from orc import model as m
from orc.dal import net


@pytest.mark.plugins(entrance_sensor)
def test_walk_in_rolls_up_under_the_entrance_entry(house):
    house.motion("active")
    house.tick(seconds=30)
    house.motion("inactive")
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
    house.motion("active")
    house.tick(seconds=30)
    house.motion("inactive")
    house.tick(minutes=2)
    house.tick(minutes=1)
    house.motion("active")
    house.tick(seconds=30)
    house.motion("inactive")
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
        ),
        (
            entrance,
            "Entrance sensor triggered",
            [
                "Applying `Day` rules",
                "Entrance sensor triggered",
                "Motion cleared, running `All Lights Off`, cleanup in 2 minutes",
                "`All Lights Off`",
                "`Silence`",
                "Trigger sensor off: skip (people present)",
            ],
        ),
    ]


@pytest.mark.plugins(entrance_sensor)
def test_a_tag_heard_after_the_door_keeps_the_listener_home(house):
    house.ble("Rex")
    house.tick(hours=1)
    with patch.object(net, "scan_presence", return_value=(set(), [])):
        house.motion("active")
        house.tick(seconds=30)
        house.motion("inactive")
        house.tick(seconds=20)
        house.ble("Rex")
        house.tick(minutes=2)

    entrance = house.broker(orc.Sensor.ENTRANCE_SENSOR)
    assert house.log() == [
        (m.Query("ble"), "Presence detected: `Rex`", []),
        (
            entrance,
            "Entrance sensor triggered",
            [
                "Applying `Day` rules",
                "Entrance sensor triggered",
                "Motion cleared, running `All Lights Off`, cleanup in 2 minutes",
                "`All Lights Off`",
                "Queued: `Dog` (until 04:09)",
                "Trigger sensor off: skip (listener home)",
            ],
        ),
    ]


@pytest.mark.plugins(entrance_sensor)
def test_a_tag_heard_only_before_the_door_is_purged(house):
    house.ble("Rex")
    house.tick(hours=1)
    with patch.object(net, "scan_presence", return_value=(set(), [])):
        house.motion("active")
        house.tick(seconds=30)
        house.motion("inactive")
        house.tick(minutes=2)

    entrance = house.broker(orc.Sensor.ENTRANCE_SENSOR)
    assert house.log() == [
        (m.Query("ble"), "Presence detected: `Rex`", []),
        (
            entrance,
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
        ),
    ]
