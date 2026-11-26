import pytest
from orc_extras import entrance_sensor, react

import orc

WALK_IN = [
    "Applying `Day` rules",
    "Running `All Lights Off`",
]
ARRIVE = [
    "`ENTRANCE_SENSOR` active → set `Living room AC` cool:low:75",
    "`ENTRANCE_SENSOR` active → set `LIVING_ROOM` on",
]
LISTENER_HOME = ["Queued: `Dog` (until 04:09)", "Trigger sensor off: skip (listener home)"]


@pytest.mark.plugins(entrance_sensor)
def test_walk_in_rolls_up_under_the_entrance_entry(house):
    house.walk_in()

    entrance = house.broker(orc.Sensor.ENTRANCE_SENSOR)
    assert house.log() == [
        (
            entrance,
            "Entrance sensor triggered",
            [
                *WALK_IN,
                "Presence detected: `Alice`",
                "Running `Silence`",
                "Trigger sensor off: skip (people present)",
            ],
        )
    ]


@pytest.mark.plugins(entrance_sensor)
def test_a_second_walk_in_starts_its_own_entry(house):
    for _ in range(2):
        house.walk_in()
        house.tick(minutes=1)

    assert [action for _, action, _ in house.log()] == ["Entrance sensor triggered", "Entrance sensor triggered"]


@pytest.mark.plugins(entrance_sensor)
@pytest.mark.parametrize("minutes, entries", [(1, 1), (3, 2)])
def test_a_cleaned_up_visit_starts_a_new_entry(house, minutes, entries):
    house.entrance_sensor("active")
    house.drop(orc.Sensor.ENTRANCE_SENSOR, "motion", "inactive")
    house.tick(minutes=minutes)
    house.entrance_sensor("active")

    assert len(house.log()) == entries


@pytest.mark.plugins(entrance_sensor)
def test_a_broadcast_during_the_pause_is_ignored(house):
    house.come_home("Rex")
    house.walk_in(seconds=20)
    house.advertise("Rex")
    house.tick(minutes=2)

    assert house.log()[1] == (house.broker(orc.Sensor.ENTRANCE_SENSOR), "Entrance sensor triggered", [*WALK_IN, *LISTENER_HOME])
    assert house.pushed == []


@pytest.mark.plugins(entrance_sensor)
def test_a_listener_who_left_before_the_door_is_lost_at_cleanup(house):
    house.come_home("Rex")
    house.leave("Rex")
    house.walk_in()

    assert house.log()[-1] == (
        house.broker(orc.Sensor.ENTRANCE_SENSOR),
        "Entrance sensor triggered",
        [
            *WALK_IN,
            "Presence lost: `Rex`",
            "Snapshot for `entrance_sensor` until 16:47: 💡 0 · 🔊 0",
            "Trigger sensor off: applying OFF",
        ],
    )
    assert house.pushed == []


@pytest.mark.plugins(entrance_sensor)
def test_a_walk_in_with_nobody_tracked_before_or_after_is_pushed(house):
    house.lan.clear()
    house.walk_in()

    assert house.log()[-1][2][-2:] == ["Trigger sensor off: applying OFF", "Entrance motion with nobody tracked before or after"]
    assert house.pushed == ["Entrance motion with nobody tracked before or after"]


@pytest.mark.plugins(entrance_sensor)
def test_the_probe_finds_a_quiet_tag(house):
    house.come_home("Rex")
    house.walk_in()

    assert house.log()[1] == (house.broker(orc.Sensor.ENTRANCE_SENSOR), "Entrance sensor triggered", [*WALK_IN, *LISTENER_HOME])


@pytest.mark.plugins(entrance_sensor)
def test_a_tag_passing_through_mid_visit_counts_for_neither(house):
    house.lan.clear()
    house.walk_in(seconds=20)
    house.advertise("Rex")
    house.tick(seconds=20)
    house.leave("Rex")
    house.walk_in()

    entrance = house.broker(orc.Sensor.ENTRANCE_SENSOR)
    assert house.log() == [
        (
            entrance,
            "Entrance sensor triggered",
            [
                *WALK_IN,
                *WALK_IN,
                "Snapshot for `entrance_sensor` until 15:48: 💡 0 · 🔊 0",
                "Trigger sensor off: applying OFF",
                "Entrance motion with nobody tracked before or after",
            ],
        )
    ]


@pytest.mark.plugins(entrance_sensor, react)
def test_react_lines_stay_under_the_walk_in_row_until_the_cleanup(house):
    house.walk_in(seconds=60)
    house.entrance_sensor("active")

    entrance = house.broker(orc.Sensor.ENTRANCE_SENSOR)
    assert house.log() == [
        (
            entrance,
            "Entrance sensor triggered",
            [
                "Applying `Day` rules",
                *ARRIVE,
                "Running `All Lights Off`",
                "`ENTRANCE_SENSOR` inactive → set `Living room AC` off",
                "Applying `Day` rules",
                *ARRIVE,
            ],
        )
    ]


@pytest.mark.plugins(entrance_sensor)
def test_a_tag_heard_in_the_quiet_minutes_waits_for_its_next_broadcast(house):
    house.come_home("Rex")
    assert house.ctx.api.present_names() == {"Rex"}
    house.walk_in(seconds=20)
    house.advertise("Rex")
    house.leave("Rex")
    assert house.ctx.api.present_names() == set()
    house.tick(minutes=2)
    assert house.ctx.api.present_names() == set()
    house.advertise("Rex")
    assert house.ctx.api.present_names() == {"Rex"}


@pytest.mark.plugins(entrance_sensor)
def test_a_second_motion_keeps_the_before_set(house):
    house.come_home("Rex")
    house.walk_in(seconds=20)
    house.leave("Rex")
    house.walk_in()

    assert house.log()[-1][2][-1] == "Trigger sensor off: applying OFF"
    assert house.pushed == []


@pytest.mark.plugins(entrance_sensor)
def test_a_rescan_during_the_visit_logs_who_it_finds(house):
    house.scan()
    house.tick(minutes=1)
    house.entrance_sensor("active")
    house.tick(seconds=30)

    house.press("/api/presence/run")

    assert house.log()[-1] == (
        orc.m.Manual("presence"),
        "Presence rescan",
        ["Presence lost: `Alice`", "Presence detected: `Alice`"],
    )


@pytest.mark.plugins(entrance_sensor)
def test_an_expire_during_the_visit_logs_the_loss(house):
    house.scan()
    house.tick(minutes=1)
    house.entrance_sensor("active")
    house.tick(seconds=30)
    house.press("/api/presence/run")
    house.tick(seconds=10)

    house.press("/api/presence/Alice/expire")

    assert house.log()[-1] == (orc.m.Manual("Alice"), "Presence expired: `Alice`", ["Presence lost: `Alice`"])


@pytest.mark.plugins(entrance_sensor)
def test_a_checkin_during_the_visit_logs_the_detection(house):
    house.entrance_sensor("active")
    house.tick(seconds=30)

    house.press("/api/presence/Alice/checkin")

    assert house.log()[-1] == (orc.m.Manual("Alice"), "Presence checked in: `Alice`", ["Presence detected: `Alice`"])
