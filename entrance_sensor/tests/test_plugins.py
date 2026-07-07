from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch
from zoneinfo import ZoneInfo

import pytest
from orc_entrance_sensor import plugins

_UTC = ZoneInfo("UTC")
_DAYTIME = datetime(2024, 1, 1, 15, tzinfo=_UTC)  # 3pm, within day_start=10/day_end=22
_NIGHTTIME = datetime(2024, 1, 1, 3, tzinfo=_UTC)  # 3am, outside that range


def _rows(*devices):
    return [SimpleNamespace(device=d, state="on") for d in devices]


@pytest.fixture
def ctx():
    mock = MagicMock()
    mock.api.JOBSTORE_MEMORY = "memory"
    mock.config.tz = _UTC
    return mock


@pytest.fixture
def sensor():
    day = SimpleNamespace(
        entrance_light=_rows("day_bulb"),
        entrance_config=_rows("day_lamp1", "day_lamp2"),
        after_hours=_rows("day_cc"),
        core_hours=_rows("day_cc2"),
        shutdown=_rows("day_light"),
    )
    night = SimpleNamespace(
        entrance_light=_rows("night_bulb"),
        entrance_config=_rows("night_lamp"),
        after_hours=_rows("night_cc"),
        core_hours=_rows("night_cc2"),
        shutdown=_rows("night_light"),
    )
    return SimpleNamespace(
        entrance_id=16,
        active_event="active",
        inactive_event="inactive",
        day_start=10,
        day_end=22,
        cleanup_delay_minutes=2,
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
    _trigger_sensor(ctx, sensor, "99", "active")
    ctx.api.execute.assert_not_called()


def test_trigger_sensor_active_daytime_executes_day_phase(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    _trigger_sensor(ctx, sensor, "16", "active")
    assert ctx.api.execute.call_count == 2
    assert ctx.model.Config.call_args_list[0] == call("day_bulb", "on")


def test_trigger_sensor_active_nighttime_uses_night_phase(ctx, sensor):
    ctx.api.local_now.return_value = _NIGHTTIME
    _trigger_sensor(ctx, sensor, "16", "active")
    assert ctx.api.execute.call_count == 2
    assert ctx.model.Config.call_args_list[0] == call("night_bulb", "on")


def test_trigger_sensor_inactive_squishes_light_off(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    _trigger_sensor(ctx, sensor, "16", "inactive")
    ctx.model.squish_configs.assert_called_once()


def test_trigger_sensor_inactive_schedules_cleanup_job(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    _trigger_sensor(ctx, sensor, "16", "inactive")
    ctx.scheduler.add_job.assert_called_once()
    _, kwargs = ctx.scheduler.add_job.call_args
    assert kwargs["id"] == "trigger-sensor"
    assert kwargs["replace_existing"] is True


def test_trigger_sensor_unknown_event_is_noop(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    _trigger_sensor(ctx, sensor, "16", "other")
    ctx.api.execute.assert_not_called()
    ctx.scheduler.add_job.assert_not_called()


# --- _run_trigger_sensor_off ---


def test_off_nighttime_executes_after_hours(sensor, plugin_ctx):
    plugin_ctx.api.local_now.return_value = _NIGHTTIME
    with patch.object(plugins, "build_ctx", return_value=plugin_ctx):
        _run_trigger_sensor_off(sensor, ctx=MagicMock())
    plugin_ctx.api.execute.assert_called_once()
    plugin_ctx.api.log.assert_called_once_with(_NIGHTTIME, plugin_ctx.model.LogSource.SYSTEM, sensor.log_after_hours)


def test_off_daytime_present_executes_after_hours(sensor, plugin_ctx):
    plugin_ctx.api.local_now.return_value = _DAYTIME
    plugin_ctx.api.check_presence.return_value = {"alice"}
    with patch.object(plugins, "build_ctx", return_value=plugin_ctx):
        _run_trigger_sensor_off(sensor, ctx=MagicMock())
    plugin_ctx.api.execute.assert_called_once()
    plugin_ctx.api.log.assert_called_once_with(_DAYTIME, plugin_ctx.model.LogSource.SYSTEM, sensor.log_present)


def test_off_daytime_sounds_executes_core_hours(sensor, plugin_ctx):
    plugin_ctx.api.local_now.return_value = _DAYTIME
    sound = MagicMock(content="audio")
    plugin_ctx.api.capture_sounds.return_value = MagicMock(items=[sound])
    with patch.object(plugins, "build_ctx", return_value=plugin_ctx):
        _run_trigger_sensor_off(sensor, ctx=MagicMock())
    plugin_ctx.api.execute.assert_called_once()
    plugin_ctx.api.log.assert_called_once_with(_DAYTIME, plugin_ctx.model.LogSource.SYSTEM, sensor.log_core_hours)


def test_off_daytime_empty_executes_shutdown_then_core_hours(sensor, plugin_ctx):
    plugin_ctx.api.local_now.return_value = _DAYTIME
    with patch.object(plugins, "build_ctx", return_value=plugin_ctx):
        _run_trigger_sensor_off(sensor, ctx=MagicMock())
    assert plugin_ctx.api.execute.call_count == 2
    plugin_ctx.api.log.assert_called_once_with(_DAYTIME, plugin_ctx.model.LogSource.SYSTEM, sensor.log_shutdown)


def test_off_expires_presence_before_checking(sensor, plugin_ctx):
    plugin_ctx.api.local_now.return_value = _DAYTIME
    plugin_ctx.api.last_seen.return_value = iter(["alice", "bob"])
    with patch.object(plugins, "build_ctx", return_value=plugin_ctx):
        _run_trigger_sensor_off(sensor, ctx=MagicMock())
    plugin_ctx.api.expire_presence.assert_called_once_with(["alice", "bob"])
