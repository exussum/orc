from datetime import time, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, create_autospec, patch

import pytest
from orc_extras import calendar, entrance_sensor
from orc_extras.calendar import Feed
from orc_extras.entrance_sensor import Settings, Timed

import orc
from orc import api
from orc import model as m
from orc.kernel import engine
from orc.model import DeviceEnum, Devices

FIXTURE = Path(__file__).parent / "fixture"


class Light(DeviceEnum):
    lamp = 1


class Chromecast(DeviceEnum):
    cc = 1


class Sensor(DeviceEnum):
    entrance = "front door motion sensor"
    patio = "balcony door"


LIGHTS_OFF = engine.Command(Devices(Light), "off")
SILENCE = engine.Command(Devices(Chromecast), "stop")
DOG = engine.Command(Devices(Chromecast), "resume")
RESET = engine.Command(Devices(Light), "on")
DAY = engine.Command(Devices(Light), 20)
NIGHT = engine.Command(Devices(Light), 1)


@pytest.fixture(autouse=True)
def _device_enums(monkeypatch):
    from orc.kernel import declarations

    enums = {"Light": Light, "Chromecast": Chromecast, "Sensor": Sensor}
    monkeypatch.setattr(orc.config, "registry", declarations.Declarations().build(enums))


@pytest.fixture
def entrance_ctx(ctx):
    ctx.config.registry = orc.config.registry
    ctx.config.devices = orc.config.devices
    ctx.config.plugin_configs = {entrance_sensor.CONFIG: (FIXTURE / "entrance_sensor.orc").read_text()}
    ctx.config.people = {"Rex": []}
    ctx.config.ble_tags = {}
    ctx.config.reset_config = SimpleNamespace(commands=(RESET,))
    ctx.config.routines = {"ROUTINE_RESET": SimpleNamespace(commands=(LIGHTS_OFF,))}
    ctx.config.ad_hoc_routines = {
        "Lights Off": m.AdhocAction(LIGHTS_OFF, reset=False),
        "Silence": m.AdhocAction(SILENCE, reset=False),
        "Dog": m.AdhocAction(DOG, delay=timedelta(minutes=6)),
        "Day Scene": m.AdhocAction(DAY, reset=False),
        "Night Scene": m.AdhocAction(NIGHT, reset=False),
    }
    return ctx


def test_entrance_config_loads(entrance_ctx):
    entrance_sensor.setup(entrance_ctx)
    sensor = entrance_ctx.api.add_listener.call_args.args[0].args[1]
    assert sensor.setting == Settings(
        cleanup_delay_minutes=2,
        entrance=Sensor.entrance,
        patio_door=Sensor.patio,
        active_event="active",
        inactive_event="inactive",
        snapshot=45,
        listener="Rex",
    )
    assert sensor.message.log_shutdown == "Trigger sensor off: applying OFF"
    assert sensor.rules.shutdown == (LIGHTS_OFF,)
    assert sensor.rules.absent == (RESET, DOG)  # Dog's reset base is composed in; its delay is ignored
    assert sensor.timed["Night"] == [Timed(start=time(22, 0), stop=time(8, 0), commands=(NIGHT,))]


def test_entrance_ble_needs_slower_cleanup(entrance_ctx):
    entrance_ctx.config.plugin_configs = {entrance_sensor.CONFIG: (FIXTURE / "entrance_sensor_fast.orc").read_text()}
    entrance_ctx.config.ble_tags = {"Rex": object()}
    with pytest.raises(ValueError, match="cleanup_delay_minutes"):
        entrance_sensor.setup(entrance_ctx)


def test_calendar_config_loads():
    ctx = MagicMock()
    ctx.api = create_autospec(api)
    ctx.config.plugin_configs = {calendar.CONFIG: (FIXTURE / "calendar.orc").read_text()}
    with patch.object(calendar.plugins, "schedule_cron") as schedule_cron:
        calendar.setup(ctx)
    _ctx, _backend, setting, feed = schedule_cron.call_args.args
    assert setting == calendar.Settings(
        backend="orc_extras.calendar.dal.stub",
        cron="10,25,40,55 8-21 * * *",
        window_hours=20,
        max_events=50,
        warning_minutes=2,
        http_timeout=120,
    )
    assert feed == [Feed("work", "ICS_URL"), Feed("personal", "ICS_URL_PERSONAL")]
