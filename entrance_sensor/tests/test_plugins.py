from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from orc_entrance_sensor import plugins

from orc import api
from orc import model as m
from orc.model import DeviceEnum

_UTC = ZoneInfo("UTC")
_DAYTIME = datetime(2024, 1, 1, 15, tzinfo=_UTC)  # 3pm, within day_start=10/day_end=22
_NIGHTTIME = datetime(2024, 1, 1, 3, tzinfo=_UTC)  # 3am, outside that range
_FUTURE = datetime(2100, 1, 1, tzinfo=_UTC)
_PAST = datetime(2000, 1, 1, tzinfo=_UTC)


class Light(DeviceEnum):
    day_bulb = 1
    day_lamp1 = 2
    day_lamp2 = 3
    day_cc = 4
    day_cc2 = 5
    day_light = 6
    night_bulb = 7
    night_lamp = 8
    night_cc = 9
    night_cc2 = 10
    night_light = 11
    saved = 12


def _rows(*devices, state=m.ON):
    return [SimpleNamespace(device=d, state=state) for d in devices]


def _snapshot(*configs, end=_FUTURE):
    return m.SnapShot(routine=m.Configs(*configs), end=end)


@pytest.fixture
def ctx():
    mock = MagicMock()
    mock.model = m
    mock.snapshot_manager = api.SnapshotManager()
    mock.api.JOBSTORE_MEMORY = "memory"
    mock.config.tz = _UTC
    return mock


@pytest.fixture
def sensor():
    day = SimpleNamespace(
        entrance_light_on=_rows(Light.day_bulb),
        entrance_light_off=_rows(Light.day_bulb, Light.night_bulb, state=m.OFF),
        entrance_config=_rows(Light.day_lamp1, Light.day_lamp2),
        after_hours=_rows(Light.day_cc),
        core_hours=_rows(Light.day_cc2),
        shutdown=_rows(Light.day_light),
    )
    night = SimpleNamespace(
        entrance_light_on=_rows(Light.night_bulb),
        entrance_light_off=_rows(Light.day_bulb, Light.night_bulb, state=m.OFF),
        entrance_config=_rows(Light.night_lamp),
        after_hours=_rows(Light.night_cc),
        core_hours=_rows(Light.night_cc2),
        shutdown=_rows(Light.night_light),
    )
    return SimpleNamespace(
        entrance_id=16,
        active_event="active",
        inactive_event="inactive",
        day_start=10,
        day_end=22,
        cleanup_delay_minutes=2,
        snapshot=45,
        log_after_hours="skip (nighttime)",
        log_present="skip (present)",
        log_core_hours="skip (sounds)",
        log_shutdown="applying OFF",
        day=day,
        night=night,
    )


@pytest.fixture
def plugin_ctx():
    mock = MagicMock()
    mock.model = m
    mock.api.last_seen.return_value = []
    mock.api.check_presence.return_value = set()
    mock.api.capture_sounds.return_value = MagicMock(items=[])
    return mock


# Unwrap decorators to call underlying functions directly
_trigger_sensor = plugins.trigger_sensor.__wrapped__
_run_trigger_sensor_off = plugins._run_trigger_sensor_off.__wrapped__


# --- trigger_sensor ---


def test_trigger_sensor_wrong_device_id_is_noop(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    ctx.snapshot_manager.snapshots[plugins.SNAPSHOT_NAME] = _snapshot(m.Config(Light.saved, m.OFF))
    _trigger_sensor(ctx, sensor, "99", "active")
    ctx.api.execute.assert_not_called()
    assert plugins.SNAPSHOT_NAME in ctx.snapshot_manager.snapshots


def test_trigger_sensor_active_daytime_executes_day_phase(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    _trigger_sensor(ctx, sensor, "16", "active")
    ctx.api.execute.assert_called_once_with(
        m.Configs(m.Config(Light.day_bulb, m.ON), m.Config(Light.day_lamp1, m.ON), m.Config(Light.day_lamp2, m.ON))
    )


def test_trigger_sensor_active_nighttime_executes_night_phase(ctx, sensor):
    ctx.api.local_now.return_value = _NIGHTTIME
    _trigger_sensor(ctx, sensor, "16", "active")
    ctx.api.execute.assert_called_once_with(m.Configs(m.Config(Light.night_bulb, m.ON), m.Config(Light.night_lamp, m.ON)))


def test_trigger_sensor_active_merges_valid_snapshot(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    ctx.snapshot_manager.snapshots[plugins.SNAPSHOT_NAME] = _snapshot(m.Config(Light.saved, m.OFF), m.Config(Light.day_bulb, m.OFF))
    _trigger_sensor(ctx, sensor, "16", "active")
    executed = ctx.api.execute.call_args[0][0]
    assert {c.what: c.state for c in executed.items} == {
        Light.saved: m.OFF,  # restored from snapshot
        Light.day_bulb: m.ON,  # phase config wins over snapshot
        Light.day_lamp1: m.ON,
        Light.day_lamp2: m.ON,
    }
    assert not ctx.snapshot_manager.snapshots  # consumed


def test_trigger_sensor_active_discards_expired_snapshot(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    ctx.snapshot_manager.snapshots[plugins.SNAPSHOT_NAME] = _snapshot(m.Config(Light.saved, m.OFF), end=_PAST)
    _trigger_sensor(ctx, sensor, "16", "active")
    ctx.api.execute.assert_called_once_with(
        m.Configs(m.Config(Light.day_bulb, m.ON), m.Config(Light.day_lamp1, m.ON), m.Config(Light.day_lamp2, m.ON))
    )
    assert not ctx.snapshot_manager.snapshots


def test_trigger_sensor_inactive_executes_light_off_rows(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    _trigger_sensor(ctx, sensor, "16", "inactive")
    ctx.api.execute.assert_called_once_with(m.Configs(m.Config(Light.day_bulb, m.OFF), m.Config(Light.night_bulb, m.OFF)))


def test_trigger_sensor_inactive_schedules_cleanup_job(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    _trigger_sensor(ctx, sensor, "16", "inactive")
    ctx.scheduler.add_job.assert_called_once()
    _, kwargs = ctx.scheduler.add_job.call_args
    assert kwargs["id"] == "trigger-sensor"
    assert kwargs["replace_existing"] is True


def test_trigger_sensor_unknown_event_is_noop(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    ctx.snapshot_manager.snapshots[plugins.SNAPSHOT_NAME] = _snapshot(m.Config(Light.saved, m.OFF))
    _trigger_sensor(ctx, sensor, "16", "other")
    ctx.api.execute.assert_not_called()
    ctx.scheduler.add_job.assert_not_called()
    assert plugins.SNAPSHOT_NAME in ctx.snapshot_manager.snapshots


# --- _run_trigger_sensor_off ---


def test_off_nighttime_executes_after_hours(sensor, plugin_ctx):
    plugin_ctx.api.local_now.return_value = _NIGHTTIME
    with patch.object(plugins, "build_ctx", return_value=plugin_ctx):
        _run_trigger_sensor_off(sensor, ctx=MagicMock())
    plugin_ctx.api.execute.assert_called_once_with(m.Configs(m.Config(Light.night_cc, m.ON)))
    plugin_ctx.api.log.assert_called_once_with(_NIGHTTIME, m.LogSource.SYSTEM, sensor.log_after_hours)


def test_off_daytime_present_executes_after_hours(sensor, plugin_ctx):
    plugin_ctx.api.local_now.return_value = _DAYTIME
    plugin_ctx.api.check_presence.return_value = {"alice"}
    with patch.object(plugins, "build_ctx", return_value=plugin_ctx):
        _run_trigger_sensor_off(sensor, ctx=MagicMock())
    plugin_ctx.api.execute.assert_called_once_with(m.Configs(m.Config(Light.day_cc, m.ON)))
    plugin_ctx.api.log.assert_called_once_with(_DAYTIME, m.LogSource.SYSTEM, sensor.log_present)


def test_off_daytime_sounds_executes_core_hours(sensor, plugin_ctx):
    plugin_ctx.api.local_now.return_value = _DAYTIME
    plugin_ctx.api.capture_sounds.return_value = MagicMock(items=[MagicMock(content="audio")])
    with patch.object(plugins, "build_ctx", return_value=plugin_ctx):
        _run_trigger_sensor_off(sensor, ctx=MagicMock())
    plugin_ctx.api.execute.assert_called_once_with(m.Configs(m.Config(Light.day_cc2, m.ON)))
    plugin_ctx.api.log.assert_called_once_with(_DAYTIME, m.LogSource.SYSTEM, sensor.log_core_hours)


def test_off_daytime_empty_snapshots_shutdown_then_executes_core_hours(sensor, plugin_ctx):
    plugin_ctx.api.local_now.return_value = _DAYTIME
    with patch.object(plugins, "build_ctx", return_value=plugin_ctx):
        _run_trigger_sensor_off(sensor, ctx=MagicMock())
    plugin_ctx.snapshot_manager.replace_config.assert_called_once_with(
        plugins.SNAPSHOT_NAME, m.Configs(m.Config(Light.day_light, m.ON)), _DAYTIME + timedelta(minutes=45)
    )
    plugin_ctx.api.execute.assert_called_once_with(m.Configs(m.Config(Light.day_cc2, m.ON)))
    plugin_ctx.api.log.assert_called_once_with(_DAYTIME, m.LogSource.SYSTEM, sensor.log_shutdown)


def test_off_expires_presence_before_checking(sensor, plugin_ctx):
    plugin_ctx.api.local_now.return_value = _DAYTIME
    plugin_ctx.api.last_seen.return_value = iter(["alice", "bob"])
    with patch.object(plugins, "build_ctx", return_value=plugin_ctx):
        _run_trigger_sensor_off(sensor, ctx=MagicMock())
    plugin_ctx.api.expire_presence.assert_called_once_with(["alice", "bob"])
