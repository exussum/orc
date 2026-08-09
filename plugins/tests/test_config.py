from collections import namedtuple
from datetime import time
from pathlib import Path

import pytest

import orc
from orc.loader import load_plugin_config
from orc.model import DeviceEnum

FIXTURE = Path(__file__).parent / "fixture"

GRAMMAR = """
setting <key> <value>
message <log> <message>
rules <trigger> <device> <state>
timed define <name> <start> <stop>
timed append <name> <device> <state>
"""

Settings = namedtuple("Settings", "entrance_id snapshot")
Messages = namedtuple("Messages", "log_present")
Rule = namedtuple("Rule", "device state")
Timed = namedtuple("Timed", "start stop device state")


class Light(DeviceEnum):
    lamp = 1


class Chromecast(DeviceEnum):
    cc = 1


@pytest.fixture(autouse=True)
def _device_enums(monkeypatch):
    monkeypatch.setattr(orc, "device_enums", [Light, Chromecast], raising=False)


def test_entrance_config_loads():
    config = load_plugin_config(
        "entrance",
        {"entrance": (FIXTURE / "entrance_sensor.orc").read_text()},
        GRAMMAR,
        serializers={"setting": Settings, "message": Messages, "rules": Rule, "timed": Timed},
        scalars=("setting", "message"),
        grouped=("rules", "timed"),
    )
    assert config.setting == Settings(entrance_id=1, snapshot=45)
    assert config.message == Messages(log_present="skip (people present)")
    assert config.rules["enter"] == [Rule(device=Light, state="on"), Rule(device=Chromecast, state="pause")]
    assert config.rules["inside"] == [Rule(device=Light, state="off")]
    assert config.timed["Day"] == [Timed(start=time(8, 0), stop=time(22, 0), device=Light, state=20)]
