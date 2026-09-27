import pytest
from orc_extras import lg_ac, react
from orc_extras.lg_ac.model import ACState

import orc
from orc import model as m
from orc.kernel import engine

AC_ID = "clip-1"


def _ac_reports(house, power, mode=None, fan=None, temperature=None):
    state = ACState(power=power, mode=mode, fan_mode=fan, temperature=temperature)
    report = " ".join(str(part) for part in (power, mode, fan, temperature) if part is not None)
    lg_ac._on_event(house.ctx, AC_ID, f"AC {AC_ID}: {report}", state)


@pytest.mark.plugins(react)
def test_rapid_broker_events_roll_up_and_a_late_one_starts_its_own_entry(house):
    house.entrance_sensor("active")
    house.tick(seconds=1)
    _ac_reports(house, "ON", mode="cool", fan="low", temperature=75)
    house.tick(seconds=1)
    house.entrance_sensor("inactive")
    house.tick(seconds=1)
    _ac_reports(house, "OFF")
    house.tick(seconds=11)
    house.entrance_sensor("active")

    entrance = house.broker(orc.Sensor.ENTRANCE_SENSOR)
    assert house.log() == [
        (
            entrance,
            "`ENTRANCE_SENSOR` active → set `Living room AC` cool:low:75",
            [
                "`ENTRANCE_SENSOR` active → set `LIVING_ROOM` on",
                "AC clip-1: ON cool low 75",
                "`ENTRANCE_SENSOR` inactive → set `Living room AC` off",
                "AC clip-1: OFF",
            ],
        ),
        (
            entrance,
            "`ENTRANCE_SENSOR` active → set `Living room AC` cool:low:75",
            ["`ENTRANCE_SENSOR` active → set `LIVING_ROOM` on"],
        ),
    ]


def _cmd(device, value):
    return engine.Command(m.Devices(device), value, tag=m.Tag.SYSTEM)


@pytest.mark.plugins(react)
def test_chromecast_pauses_dispatch_after_the_lights(house):
    house.report(orc.Light.BEDROOM_LAMP, "switch", "on")

    assert house.log() == [
        (
            house.broker(orc.Light.BEDROOM_LAMP),
            "`BEDROOM_LAMP` on → set `LIVING_ROOM`, `BEDROOM` pause",
            [
                "`BEDROOM_LAMP` on → set `LIVING_ROOM` on",
                "`BEDROOM_LAMP` on → set `KITCHEN` on",
                "`BEDROOM_LAMP` on → set `HALL` on",
                "`BEDROOM_LAMP` on → set `PORCH` off",
                "`BEDROOM_LAMP` on → set `OFFICE` off",
                "`BEDROOM_LAMP` on → set `OFFICE` on",
                "`OFFICE` given conflicting states in one run: off, on",
            ],
        )
    ]
    assert house.dispatched[-1] == (
        _cmd(orc.Light.LIVING_ROOM, m.ON),
        _cmd(orc.Light.KITCHEN, m.ON),
        _cmd(orc.Light.HALL, m.ON),
        _cmd(orc.Light.OFFICE, m.ON),
        _cmd(orc.Light.PORCH, m.OFF),
        _cmd(orc.Chromecast.LIVING_ROOM, m.PAUSE),
        _cmd(orc.Chromecast.BEDROOM, m.PAUSE),
    )
