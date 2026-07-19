from datetime import datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from orc_plugins.entrance_sensor import plugins

from orc import api
from orc import model as m
from orc.model import DeviceEnum

_UTC = ZoneInfo("UTC")
_DAYTIME = datetime(2024, 1, 1, 15, tzinfo=_UTC)  # 3pm, inside the Day window (8:00-22:00)
_NIGHTTIME = datetime(2024, 1, 1, 3, tzinfo=_UTC)  # 3am, inside the Night window
_FUTURE = datetime(2100, 1, 1, tzinfo=_UTC)


class Light(DeviceEnum):
    day_bulb = 1
    night_bulb = 2
    lamp = 3
    saved = 4


class Chromecast(DeviceEnum):
    cc = 1


def _row(device, state, start="", stop=""):
    return SimpleNamespace(device=device, state=state, start=start, stop=stop)


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


# Mirrors the shape of the provisioned config: walking in pauses the media and
# lights the entrance for the current window; the Night window stops the media
# outright; the cleanup job settles the house depending on who is home.
@pytest.fixture
def sensor():
    timed = SimpleNamespace(
        Day=[_row(Light.day_bulb, 20, start=time(8), stop=time(22))],
        Night=[
            _row(Light.night_bulb, 1, start=time(22), stop=time(8)),
            _row(Chromecast.cc, m.STOP),
        ],
    )
    rules = SimpleNamespace(
        enter=[_row(Light.lamp, m.ON), _row(Chromecast.cc, m.PAUSE)],
        inside=[_row(Light.day_bulb, m.OFF), _row(Light.night_bulb, m.OFF)],
        present=[_row(Chromecast.cc, m.STOP)],
        absent=[_row(Chromecast.cc, m.RESUME)],
        shutdown=[_row(Light.lamp, m.OFF)],
    )
    return SimpleNamespace(
        entrance_id=16,
        active_event="active",
        inactive_event="inactive",
        cleanup_delay_minutes=2,
        snapshot=45,
        log_present="skip (present)",
        log_absent="skip (sounds)",
        log_shutdown="applying OFF",
        rules=rules,
        timed=timed,
    )


@pytest.fixture
def plugin_ctx():
    mock = MagicMock()
    mock.model = m
    mock.api.last_seen.return_value = []
    mock.api.check_presence.return_value = set()
    mock.api.capture_sounds.return_value = MagicMock(items=[])
    return mock


def _cleanup(sensor, plugin_ctx):
    with patch.object(plugins, "build_ctx", return_value=plugin_ctx):
        plugins._run_trigger_sensor_off.__wrapped__(sensor, ctx=MagicMock())


# Unwrap the config-loading decorator to call the underlying function directly
_trigger_sensor = plugins.trigger_sensor.__wrapped__


# --- Walking in ---


def test_day_walk_in_brightens_entrance_and_pauses_media(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    _trigger_sensor(ctx, sensor, "16", "active")
    ctx.api.dispatch.assert_called_once_with(
        m.Configs(m.Config(Light.day_bulb, 20), m.Config(Light.lamp, m.ON), m.Config(Chromecast.cc, m.PAUSE)), force=True
    )


def test_night_walk_in_dims_entrance_and_stops_media(ctx, sensor):
    # The Night window's stop beats enter's pause: timed rows outrank enter rules
    ctx.api.local_now.return_value = _NIGHTTIME
    _trigger_sensor(ctx, sensor, "16", "active")
    ctx.api.dispatch.assert_called_once_with(
        m.Configs(m.Config(Light.night_bulb, 1), m.Config(Light.lamp, m.ON), m.Config(Chromecast.cc, m.STOP)), force=True
    )


def test_walk_in_uses_first_window_that_contains_now(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    sensor.timed = SimpleNamespace(
        Afternoon=[_row(Light.night_bulb, 50, start=time(14), stop=time(16))],
        **vars(sensor.timed),
    )
    _trigger_sensor(ctx, sensor, "16", "active")
    executed = ctx.api.dispatch.call_args[0][0]
    assert m.Config(Light.night_bulb, 50) in executed.items
    assert m.Config(Light.day_bulb, 20) not in executed.items


def test_walk_in_outside_any_window_runs_enter_only(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    sensor.timed = SimpleNamespace(Morning=[_row(Light.day_bulb, 20, start=time(8), stop=time(9))])
    _trigger_sensor(ctx, sensor, "16", "active")
    ctx.api.dispatch.assert_called_once_with(m.Configs(m.Config(Light.lamp, m.ON), m.Config(Chromecast.cc, m.PAUSE)), force=True)


def test_walk_in_shortly_after_shutdown_restores_house_lights(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    ctx.snapshot_manager.snapshots[plugins.SNAPSHOT_NAME] = _snapshot(
        m.Config(Light.saved, m.ON),  # how the house looked before shutdown
        m.Config(Light.day_bulb, m.OFF),  # entrance lights: off because the plugin turned them off
        m.Config(Light.night_bulb, m.OFF),
    )
    _trigger_sensor(ctx, sensor, "16", "active")
    executed = ctx.api.dispatch.call_args[0][0]
    assert {c.what: c.state for c in executed.items} == {
        Light.saved: m.ON,  # restored
        Light.day_bulb: 20,  # follows the current window, never the snapshot
        Light.lamp: m.ON,
        Chromecast.cc: m.PAUSE,
    }
    assert not ctx.snapshot_manager.snapshots  # consumed


def test_walk_in_cancels_pending_cleanup(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    _trigger_sensor(ctx, sensor, "16", "active")
    ctx.scheduler.remove_job.assert_called_once_with("trigger-sensor", jobstore=ctx.api.JOBSTORE_MEMORY)


def test_walk_in_with_no_pending_cleanup_does_not_cancel(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    ctx.scheduler.get_job.return_value = None
    _trigger_sensor(ctx, sensor, "16", "active")
    ctx.scheduler.remove_job.assert_not_called()
    ctx.api.dispatch.assert_called_once()


# --- Walking past, into the house ---


def test_entrance_lights_turn_off_behind_you(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    _trigger_sensor(ctx, sensor, "16", "inactive")
    ctx.api.dispatch.assert_called_once_with(
        m.Configs(m.Config(Light.day_bulb, m.OFF, trigger=m.Trigger.SYSTEM), m.Config(Light.night_bulb, m.OFF, trigger=m.Trigger.SYSTEM))
    )


def test_cleanup_is_scheduled_for_later(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    _trigger_sensor(ctx, sensor, "16", "inactive")
    ctx.scheduler.add_job.assert_called_once()
    _, kwargs = ctx.scheduler.add_job.call_args
    assert kwargs["id"] == "trigger-sensor"
    assert kwargs["replace_existing"] is True


# --- Cleanup, minutes later ---


def test_someone_home_stops_media(sensor, plugin_ctx):
    plugin_ctx.api.local_now.return_value = _DAYTIME
    plugin_ctx.api.check_presence.return_value = {"alice"}
    _cleanup(sensor, plugin_ctx)
    plugin_ctx.api.dispatch.assert_called_once_with(m.Configs(m.Config(Chromecast.cc, m.STOP)))
    plugin_ctx.api.log.assert_called_once_with(_DAYTIME, m.LogSource.PLUGIN, sensor.log_present)


def test_pet_home_alone_keeps_media_playing(sensor, plugin_ctx):
    plugin_ctx.api.local_now.return_value = _DAYTIME
    plugin_ctx.api.capture_sounds.return_value = MagicMock(items=[MagicMock(content="audio")])
    _cleanup(sensor, plugin_ctx)
    plugin_ctx.api.dispatch.assert_called_once_with(m.Configs(m.Config(Chromecast.cc, m.RESUME)))
    plugin_ctx.api.log.assert_called_once_with(_DAYTIME, m.LogSource.PLUGIN, sensor.log_absent)


def test_empty_quiet_house_shuts_down_and_snapshots(sensor, plugin_ctx):
    plugin_ctx.api.local_now.return_value = _DAYTIME
    _cleanup(sensor, plugin_ctx)
    plugin_ctx.snapshot_manager.replace_config.assert_called_once_with(
        plugins.SNAPSHOT_NAME, m.Configs(m.Config(Light.lamp, m.OFF)), _DAYTIME + timedelta(minutes=45)
    )
    plugin_ctx.api.dispatch.assert_called_once_with(m.Configs(m.Config(Chromecast.cc, m.RESUME)))
    plugin_ctx.api.log.assert_called_once_with(_DAYTIME, m.LogSource.PLUGIN, sensor.log_shutdown)


def test_presence_is_expired_before_checking(sensor, plugin_ctx):
    plugin_ctx.api.local_now.return_value = _DAYTIME
    plugin_ctx.api.last_seen.return_value = iter(["alice", "bob"])
    _cleanup(sensor, plugin_ctx)
    plugin_ctx.api.expire_presence.assert_called_once_with(["alice", "bob"])


# --- Guards ---


def test_other_devices_are_ignored(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    ctx.snapshot_manager.snapshots[plugins.SNAPSHOT_NAME] = _snapshot(m.Config(Light.saved, m.OFF))
    _trigger_sensor(ctx, sensor, "99", "active")
    ctx.api.dispatch.assert_not_called()
    assert plugins.SNAPSHOT_NAME in ctx.snapshot_manager.snapshots


def test_unknown_events_are_ignored(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    ctx.snapshot_manager.snapshots[plugins.SNAPSHOT_NAME] = _snapshot(m.Config(Light.saved, m.OFF))
    _trigger_sensor(ctx, sensor, "16", "other")
    ctx.api.dispatch.assert_not_called()
    ctx.scheduler.add_job.assert_not_called()
    assert plugins.SNAPSHOT_NAME in ctx.snapshot_manager.snapshots
