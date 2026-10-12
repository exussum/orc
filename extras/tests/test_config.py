from datetime import time, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from orc_engine import cast
from orc_engine import model as em
from orc_extras import calendar, entrance_sensor
from orc_extras.calendar import Feed
from orc_extras.entrance_sensor import Settings

import orc
from orc import model as m
from orc.kernel.declarations import Declarations
from orc.model import DeviceEnum, Devices
from tests.conftest import plugin_config


class Light(DeviceEnum):
    lamp = 1


class Chromecast(DeviceEnum):
    cc = 1


class Sensor(DeviceEnum):
    entrance = "front door motion sensor"
    patio = "balcony door"


LIGHTS_OFF = em.Command(Devices(Light), "off")
SILENCE = em.Command(Devices(Chromecast), "stop")
DOG = em.Command(Devices(Chromecast), "resume")
RESET = em.Command(Devices(Light), "on")
DAY = em.Command(Devices(Light), 20)
NIGHT = em.Command(Devices(Light), 1)
DEVICE_ENUMS = {"Light": Light, "Chromecast": Chromecast, "Sensor": Sensor}


@pytest.fixture
def entrance_ctx(ctx):
    ctx.config.registry = orc.config.registry
    ctx.config.devices = orc.config.devices
    ctx.config.plugin_configs = plugin_config(entrance_sensor, "entrance_sensor")
    ctx.config.people = {"Rex": []}
    ctx.config.ble_tags = {}
    ctx.config.reset_config = SimpleNamespace(commands=(RESET,))
    ctx.config.ad_hoc_routines = {
        "AD_HOC_LIGHTS_OFF": m.AdhocAction((LIGHTS_OFF,), name="Lights Off", reset=False),
        "AD_HOC_SILENCE": m.AdhocAction((SILENCE,), name="Silence", reset=False),
        "AD_HOC_DOG": m.AdhocAction((DOG,), name="Dog", delay=timedelta(minutes=6)),
        "AD_HOC_DAY_SCENE": m.AdhocAction((DAY,), name="Day Scene", reset=False),
        "AD_HOC_NIGHT_SCENE": m.AdhocAction((NIGHT,), name="Night Scene", reset=False),
        "AD_HOC_LAMP_OFF": m.AdhocAction((LIGHTS_OFF,), name="Lamp Off", reset=False),
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
    assert sensor.rules.shutdown == "AD_HOC_LAMP_OFF"
    assert sensor.rules.absent == "AD_HOC_DOG"
    night = em.Step(em.During(time(22, 0), time(8, 0)), NIGHT)
    assert em.Rule((night,), name="Night") in sensor.timed


def test_entrance_ble_needs_slower_cleanup(entrance_ctx):
    config = entrance_ctx.config.plugin_configs[entrance_sensor.CONFIG]
    entrance_ctx.config.plugin_configs[entrance_sensor.CONFIG] = config.replace("cleanup_delay_minutes 2", "cleanup_delay_minutes 0")
    entrance_ctx.config.ble_tags = {"Rex": object()}
    with pytest.raises(ValueError, match="cleanup_delay_minutes"):
        entrance_sensor.setup(entrance_ctx)


def test_calendar_declares_each_feed_as_a_url():
    builder = Declarations(plugin_configs=plugin_config(calendar, "calendar"))
    calendar.declare(builder)
    assert builder.secrets == {"ICS_URL": cast.url, "ICS_URL_PERSONAL": cast.url}


def test_calendar_config_loads(ctx):
    ctx.config.plugin_configs = plugin_config(calendar, "calendar")
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
