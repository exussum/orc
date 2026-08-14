from datetime import datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from orc_plugins import entrance_sensor
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
    mock.config.settings.tz = _UTC
    return mock


# Mirrors the shape of the provisioned config: walking in pauses the media and
# lights the entrance for the current window; the Night window stops the media
# outright; the cleanup job settles the house depending on who is home.
@pytest.fixture
def sensor():
    timed = {
        "Day": [_row(Light.day_bulb, 20, start=time(8), stop=time(22))],
        "Night": [
            _row(Light.night_bulb, 1, start=time(22), stop=time(8)),
            _row(Chromecast.cc, m.STOP),
        ],
    }
    rules = entrance_sensor.Rules(
        enter=[_row(Light.lamp, m.ON), _row(Chromecast.cc, m.PAUSE)],
        inside=[_row(Light.day_bulb, m.OFF), _row(Light.night_bulb, m.OFF)],
        present=[_row(Chromecast.cc, m.STOP)],
        absent=[_row(Chromecast.cc, m.RESUME)],
        shutdown=[_row(Light.lamp, m.OFF)],
    )
    return SimpleNamespace(
        setting=entrance_sensor.Settings(
            cleanup_delay_minutes=2,
            entrance_id=16,
            patio_door_id=56,
            active_event="active",
            inactive_event="inactive",
            snapshot=45,
        ),
        message=entrance_sensor.Messages(
            log_present="skip (present)",
            log_door_open="skip (door open)",
            log_absent="skip (sounds)",
            log_shutdown="applying OFF",
        ),
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
    entry = m.LogEntry(_DAYTIME, plugins.Log.ENTRANCE, "Entrance sensor triggered")
    plugins._run_trigger_sensor_off.__wrapped__(sensor, entry, ctx=plugin_ctx)
    return entry


def _trigger_sensor(ctx, sensor, device_id, event):
    old = "inactive" if event == "active" else "active"
    device = m.DeviceState(id=int(device_id), name="front door motion sensor", attributes={"motion": event}, last_activity=None)
    plugins._on_sensor_event(ctx, sensor, {sensor.setting.entrance_id, sensor.setting.patio_door_id}, device, "motion", old, event)
    queued = [c for c in ctx.scheduler.add_job.call_args_list if c.args[0] is plugins._run_motion]
    ctx.scheduler.add_job.reset_mock()
    for call in queued:
        plugins._run_motion.__wrapped__(*call.kwargs["args"], ctx=ctx)


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
    sensor.timed = {
        "Afternoon": [_row(Light.night_bulb, 50, start=time(14), stop=time(16))],
        **sensor.timed,
    }
    _trigger_sensor(ctx, sensor, "16", "active")
    executed = ctx.api.dispatch.call_args[0][0]
    assert m.Config(Light.night_bulb, 50) in executed.items
    assert m.Config(Light.day_bulb, 20) not in executed.items


def test_walk_in_outside_any_window_runs_enter_only(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    sensor.timed = {"Morning": [_row(Light.day_bulb, 20, start=time(8), stop=time(9))]}
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


def test_motion_reuses_the_latest_trigger_entry(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    entry = m.LogEntry(_DAYTIME, plugins.Log.ENTRANCE, plugins.TRIGGER_MSG)
    ctx.api.log_entries.return_value = [entry]
    _trigger_sensor(ctx, sensor, "16", "active")
    ctx.api.log.assert_not_called()
    assert [c.action for c in entry.children] == ["Applying `Day` rules"]


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
    entry = _cleanup(sensor, plugin_ctx)
    plugin_ctx.api.dispatch.assert_called_once_with(m.Configs(m.Config(Chromecast.cc, m.STOP)))
    assert [c.action for c in entry.children] == [sensor.message.log_present]


def test_pet_home_alone_keeps_media_playing(sensor, plugin_ctx):
    plugin_ctx.api.local_now.return_value = _DAYTIME
    plugin_ctx.api.capture_sounds.return_value = MagicMock(items=[MagicMock(content="audio")])
    entry = _cleanup(sensor, plugin_ctx)
    plugin_ctx.api.dispatch.assert_called_once_with(m.Configs(m.Config(Chromecast.cc, m.RESUME)))
    assert [c.action for c in entry.children] == [sensor.message.log_absent]


def test_pet_home_alone_restores_pre_visit_state(sensor, plugin_ctx):
    # An undetected visitor left: put the lights back how the dog had them
    plugin_ctx.api.local_now.return_value = _DAYTIME
    plugin_ctx.api.capture_sounds.return_value = MagicMock(items=[MagicMock(content="audio")])
    _cleanup(sensor, plugin_ctx)
    plugin_ctx.snapshot_manager.resume.assert_called_once_with(plugins.SNAPSHOT_NAME, m.Configs())


def test_empty_quiet_house_shuts_down_and_snapshots(sensor, plugin_ctx):
    plugin_ctx.api.local_now.return_value = _DAYTIME
    entry = _cleanup(sensor, plugin_ctx)
    plugin_ctx.snapshot_manager.replace_config.assert_called_once_with(
        plugins.SNAPSHOT_NAME, m.Configs(m.Config(Light.lamp, m.OFF)), _DAYTIME + timedelta(minutes=45), plugins.SNAPSHOT_NAME
    )
    plugin_ctx.api.dispatch.assert_called_once_with(m.Configs(m.Config(Chromecast.cc, m.RESUME)))
    assert [c.action for c in entry.children] == [sensor.message.log_shutdown]


def _seed_devices(plugin_ctx, *devices):
    plugin_ctx.api.device_states.return_value = list(devices)


def _door(state):
    return m.DeviceState(id=56, name="balcony door", attributes={"contact": state, "battery": "98"}, last_activity=None)


def test_open_door_counts_as_present(sensor, plugin_ctx):
    plugin_ctx.api.local_now.return_value = _DAYTIME
    _seed_devices(plugin_ctx, _door("open"))
    entry = _cleanup(sensor, plugin_ctx)
    plugin_ctx.api.dispatch.assert_called_once_with(m.Configs(m.Config(Chromecast.cc, m.STOP)))
    assert [c.action for c in entry.children] == [sensor.message.log_door_open]


def test_closed_door_still_shuts_down(sensor, plugin_ctx):
    plugin_ctx.api.local_now.return_value = _DAYTIME
    _seed_devices(plugin_ctx, _door("closed"))
    entry = _cleanup(sensor, plugin_ctx)
    assert [c.action for c in entry.children] == [sensor.message.log_shutdown]


def test_unseen_door_still_shuts_down(sensor, plugin_ctx):
    plugin_ctx.api.local_now.return_value = _DAYTIME
    _seed_devices(plugin_ctx)
    entry = _cleanup(sensor, plugin_ctx)
    assert [c.action for c in entry.children] == [sensor.message.log_shutdown]


def _device(id=16, name="front door motion sensor", battery="100", attributes=None):
    return m.DeviceState(id=id, name=name, attributes=attributes or {"battery": battery}, last_activity=None)


def test_critical_battery_report_logs(plugin_ctx, sensor):
    plugin_ctx.api.local_now.return_value = _DAYTIME
    plugins._on_sensor_event(plugin_ctx, sensor, {16}, _device(battery="5"), "battery", "5", "5")
    plugin_ctx.api.log.assert_called_once_with(plugins.Log.ENTRANCE, "Low battery on `front door motion sensor` (CRITICAL)")


def test_healthy_battery_report_does_not_log(plugin_ctx, sensor):
    plugins._on_sensor_event(plugin_ctx, sensor, {16}, _device(battery="80"), "battery", None, "80")
    plugin_ctx.api.log.assert_not_called()


def test_unwatched_device_is_ignored(plugin_ctx, sensor):
    plugins._on_sensor_event(plugin_ctx, sensor, {16}, _device(id=99, battery="5"), "battery", None, "5")
    plugin_ctx.api.log.assert_not_called()


def test_battery_state_reads_the_device_cache(plugin_ctx):
    _seed_devices(plugin_ctx, _device(battery="80"))
    assert plugins.battery_state(plugin_ctx, {16}) == [{"name": "front door motion sensor", "battery": "HIGH", "last_activity": None}]


def test_setup_registers_listener_and_bound_provider(plugin_ctx, sensor):
    sensor.rules = dict(sensor.rules._asdict())
    with patch.object(entrance_sensor, "load_plugin_config", return_value=sensor):
        entrance_sensor.setup(plugin_ctx)
    plugin_ctx.api.add_listener.assert_called_once()
    title, provider = plugin_ctx.api.add_state_provider.call_args[0]
    assert title == "Entrance Sensors"
    _seed_devices(plugin_ctx, _device(battery="80"))
    assert provider() == [{"name": "front door motion sensor", "battery": "HIGH", "last_activity": None}]


def _motion(ctx, sensor, old, new):
    device = _device(attributes={"motion": new})
    plugins._on_sensor_event(ctx, sensor, {16, 56}, device, "motion", old, new)


def test_motion_republish_does_not_fire(ctx, sensor):
    _motion(ctx, sensor, "active", "active")
    ctx.api.dispatch.assert_not_called()


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
