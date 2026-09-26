import pytest
from orc_extras import entrance_sensor

import orc


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
