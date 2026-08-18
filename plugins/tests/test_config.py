from datetime import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from orc_plugins import calendar, entrance_sensor
from orc_plugins.calendar import Feed
from orc_plugins.entrance_sensor import Rule, Settings, Timed

import orc
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
    ctx = MagicMock()
    ctx.config.plugin_configs = {entrance_sensor.CONFIG: (FIXTURE / "entrance_sensor.orc").read_text()}
    entrance_sensor.setup(ctx)
    sensor = ctx.api.add_listener.call_args.args[0].args[1]
    assert sensor.setting == Settings(
        cleanup_delay_minutes=2, entrance_id=1, patio_door_id=56, active_event="active", inactive_event="inactive", snapshot=45
    )
    assert sensor.message.log_shutdown == "Trigger sensor off: applying OFF"
    assert sensor.rules.enter == [Rule(device=Light, state="on"), Rule(device=Chromecast, state="pause")]
    assert sensor.rules.shutdown == [Rule(device=Light, state="off")]
    assert sensor.timed["Night"] == [Timed(start=time(22, 0), stop=time(8, 0), device=Light, state=1)]


def test_calendar_config_loads():
    ctx = MagicMock()
    ctx.config.plugin_configs = {calendar.CONFIG: (FIXTURE / "calendar.orc").read_text()}
    with patch.object(calendar.plugins, "schedule_cron") as schedule_cron:
        calendar.setup(ctx)
    _ctx, _backend, setting, feed = schedule_cron.call_args.args
    assert setting == calendar.Settings(
        backend="orc_plugins.calendar.dal.feed.stub",
        cron="10,25,40,55 8-21 * * *",
        window_hours=20,
        max_events=50,
        warning_minutes=2,
        http_timeout=120,
    )
    assert feed == [Feed("work", "ICS_URL"), Feed("personal", "ICS_URL_PERSONAL")]
