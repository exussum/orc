import pytest
from orc_extras import entrance_sensor

import orc
from orc import api

LOW = "Low battery on `ENTRANCE_SENSOR` (CRITICAL)"


@pytest.mark.plugins()
def test_a_door_battery_crossing_into_critical_is_pushed_once(house):
    for level in ("80", "5", "5", "8"):
        house.report(orc.Sensor.ENTRANCE_SENSOR, "battery", level)

    assert house.log() == [(house.broker(orc.Sensor.ENTRANCE_SENSOR), LOW, [])]
    assert house.pushed == ["Low battery on ENTRANCE_SENSOR (CRITICAL)"]
    assert api.log_entries()[0].notified


@pytest.mark.plugins()
def test_a_remote_battery_crossing_into_critical_is_pushed(house):
    house.report(orc.Button.LIVING_ROOM_REMOTE, "battery", "20")
    house.report(orc.Button.LIVING_ROOM_REMOTE, "battery", "10")

    assert house.pushed == ["Low battery on LIVING_ROOM_REMOTE (CRITICAL)"]


@pytest.mark.plugins(entrance_sensor)
def test_a_battery_report_during_a_walk_in_nests_and_flags_the_entry(house):
    house.report(orc.Sensor.ENTRANCE_SENSOR, "battery", "80")
    house.entrance_sensor("active")
    house.report(orc.Sensor.ENTRANCE_SENSOR, "battery", "5")

    [(trigger, action, children)] = house.log()
    assert (trigger, action) == (house.broker(orc.Sensor.ENTRANCE_SENSOR), "Entrance sensor triggered")
    assert children[-1] == LOW
    assert house.pushed == ["Low battery on ENTRANCE_SENSOR (CRITICAL)"]
    entry = api.log_entries()[0]
    assert entry.notified and entry.children[-1].notified
