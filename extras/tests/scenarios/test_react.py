import pytest
from orc_extras import react

import orc
from orc import model as m
from orc.kernel import engine


@pytest.mark.plugins(react)
def test_rapid_broker_events_roll_up(house):
    house.motion("active")
    house.tick(seconds=1)
    house.ac_reports("ON", mode="cool", fan="low", temperature=75)
    house.tick(seconds=1)
    house.motion("inactive")
    house.tick(seconds=1)
    house.ac_reports("OFF")
    house.tick(seconds=11)
    house.motion("active")

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
                "`ENTRANCE_SENSOR` active → set `Living room AC` cool:low:75",
                "`ENTRANCE_SENSOR` active → set `LIVING_ROOM` on",
            ],
        )
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
