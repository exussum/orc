import pytest
from orc_extras import entrance_sensor

import orc
from orc import model as m

WALK_IN = [
    "Applying `Day` rules",
    "Running `All Lights Off`",
]
LISTENER_HOME = ["Queued: `Dog` (until 04:09)", "Trigger sensor off: skip (listener home)"]
DELAYS = [
    pytest.param([], marks=pytest.mark.plugins(entrance_sensor), id="equal"),
    pytest.param(["Presence lost: `Rex`"], marks=pytest.mark.plugins(entrance_sensor, entrance_sensor="entrance_sensor_split"), id="split"),
]


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


@pytest.mark.parametrize("resumed", DELAYS)
def test_a_broadcast_during_the_pause_is_ignored(house, resumed):
    house.come_home("Rex")
    house.walk_in(seconds=20)
    house.advertise("Rex")
    house.tick(minutes=2)

    assert house.log()[1] == (house.broker(orc.Sensor.ENTRANCE_SENSOR), "Entrance sensor triggered", [*WALK_IN, *resumed, *LISTENER_HOME])
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
            "Snapshot for `entrance_sensor` until 04:47: all off",
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


@pytest.mark.parametrize("resumed", DELAYS)
def test_the_probe_finds_a_quiet_tag(house, resumed):
    house.come_home("Rex")
    house.walk_in()

    assert house.log()[1] == (house.broker(orc.Sensor.ENTRANCE_SENSOR), "Entrance sensor triggered", [*WALK_IN, *resumed, *LISTENER_HOME])


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
                "Snapshot for `entrance_sensor` until 03:48: all off",
                "Trigger sensor off: applying OFF",
                "Entrance motion with nobody tracked before or after",
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


@pytest.mark.plugins(entrance_sensor, entrance_sensor="entrance_sensor_split")
def test_a_tag_checks_itself_in_once_presence_resumes(house):
    house.come_home("Rex")
    house.walk_in(seconds=30)
    house.advertise("Rex")
    assert house.ctx.api.present_names() == set()
    house.tick(seconds=30)
    house.advertise("Rex")
    assert house.ctx.api.present_names() == {"Rex"}
    house.tick(minutes=1)

    assert house.log()[1:] == [
        (
            house.broker(orc.Sensor.ENTRANCE_SENSOR),
            "Entrance sensor triggered",
            [*WALK_IN, "Presence lost: `Rex`", *LISTENER_HOME],
        ),
        (m.Query("ble"), "Presence detected: `Rex`", []),
    ]
    assert house.probed == []
