from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from orc import api, config
from orc import model as m
from orc.dal import net


@asynccontextmanager
async def _reachable(address, timeout):
    yield


@pytest.mark.plugins()
def test_manual_rescan_clears_and_refinds_everyone(house):
    api.mark_present(["Alice"], api.local_now(), m.Query("lan"))
    net.presence._addresses["Rex"] = "11:22:33:44:55:66"
    net.presence._tz = config.settings.tz
    api.mark_present(["Rex"], api.local_now(), m.Query("ble"))
    house.tick(minutes=5)
    api._ACTIVITY_LOG.clear()

    with patch.object(config, "ble_tags", {"Rex": m.BleKey(bytes(32), 0)}), patch.object(net, "BleakClient", _reachable):
        api.rerun_presence_check(m.Manual("presence"))

    assert house.log() == [
        (
            m.Manual("presence"),
            "Presence rescan",
            ["Presence lost: `Alice, Rex`", "Presence detected: `Rex`", "Presence detected: `Alice`"],
        )
    ]
    assert api.present_names() == {"Alice", "Rex"}


@pytest.mark.plugins()
def test_two_rescans_an_hour_apart_are_two_rows(house):
    api.mark_present(["Alice"], api.local_now(), m.Query("lan"))
    house.tick(minutes=5)
    api._ACTIVITY_LOG.clear()

    with patch.object(config, "ble_tags", {}):
        house.press("/api/presence/run")
        house.tick(hours=1)
        house.press("/api/presence/run")

    assert house.log() == [
        (m.Manual("presence"), "Presence rescan", ["Presence lost: `Alice`", "Presence detected: `Alice`"]),
        (m.Manual("presence"), "Presence rescan", ["Presence lost: `Alice`", "Presence detected: `Alice`"]),
    ]
