from datetime import time
from pathlib import Path

import pytest
from orc_plugins import calendar, entrance_sensor
from orc_plugins.calendar import Feed
from orc_plugins.entrance_sensor import GRAMMAR, Messages, Rule, Rules, Settings, Timed

import orc
from orc.loader import load_plugin_config
from orc.model import DeviceEnum

FIXTURE = Path(__file__).parent / "fixture"


class Light(DeviceEnum):
    lamp = 1


class Chromecast(DeviceEnum):
    cc = 1


@pytest.fixture(autouse=True)
def _device_enums(monkeypatch):
    from orc import declarations

    monkeypatch.setattr(orc.config, "registry", declarations.Declarations().build({"Light": Light, "Chromecast": Chromecast}))


def test_entrance_config_loads():
    config = load_plugin_config(
        entrance_sensor.CONFIG,
        {entrance_sensor.CONFIG: (FIXTURE / "entrance_sensor.orc").read_text()},
        GRAMMAR,
        serializers={"setting": Settings, "message": Messages, "rules": Rule, "timed": Timed},
        scalars=("setting", "message"),
        grouped=("rules", "timed"),
    )
    assert config.setting == Settings(
        cleanup_delay_minutes=2, entrance_id=1, patio_door_id=56, active_event="active", inactive_event="inactive", snapshot=45
    )
    assert config.message.log_shutdown == "Trigger sensor off: applying OFF"
    rules = Rules(**config.rules)
    assert rules.enter == [Rule(device=Light, state="on"), Rule(device=Chromecast, state="pause")]
    assert rules.shutdown == [Rule(device=Light, state="off")]
    assert config.timed["Night"] == [Timed(start=time(22, 0), stop=time(8, 0), device=Light, state=1)]


def test_calendar_config_loads():
    config = load_plugin_config(
        calendar.CONFIG,
        {calendar.CONFIG: (FIXTURE / "calendar.orc").read_text()},
        calendar.GRAMMAR,
        serializers={"setting": calendar.Settings, "feed": Feed},
        scalars=("setting",),
        grouped=("feed",),
    )
    assert config.setting == calendar.Settings(
        backend="orc_plugins.calendar.stub",
        cron="10,25,40,55 8-21 * * *",
        window_hours=20,
        max_events=50,
        warning_minutes=2,
        http_timeout=120,
    )
    assert config.feed == {"work": [Feed("ICS_URL")], "personal": [Feed("ICS_URL_PERSONAL")]}
