from datetime import datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import ANY, create_autospec, patch
from zoneinfo import ZoneInfo

import pytest
from apscheduler.schedulers.base import BaseScheduler
from orc_extras import entrance_sensor
from orc_extras.entrance_sensor import plugins

from orc import model as m
from orc.kernel import engine
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


class Sensor(DeviceEnum):
    entrance = 16
    patio = 56


def _cmd(device, state):
    return engine.Command[str, m.Devices](m.Devices(device), state)


def _window(start, stop, *commands):
    return entrance_sensor.Timed(start=start, stop=stop, commands=commands)


def _snapshot(*commands, end=_FUTURE):
    return m.SnapShot(routine=tuple(commands), end=end)


def _device_state_side_effect(mock):
    return lambda target: next((s for s in mock.api.device_states.return_value if str(s.id) == target or s.name == target), None)


@pytest.fixture
def ctx(ctx):
    ctx.scheduler = create_autospec(BaseScheduler, instance=True)
    ctx.api.JOBSTORE_MEMORY = "memory"
    ctx.api.device_state.side_effect = _device_state_side_effect(ctx)
    ctx.config.settings.tz = _UTC
    return ctx


# Mirrors the shape of the provisioned config: walking in lights the entrance
# for the current window; the Night window stops the media outright; the
# cleanup job settles the house depending on who is home.
@pytest.fixture
def sensor():
    timed = {
        "Day": [_window(time(8), time(22), _cmd(Light.day_bulb, 20))],
        "Night": [_window(time(22), time(8), _cmd(Light.night_bulb, 1), _cmd(Chromecast.cc, m.STOP))],
    }
    rules = entrance_sensor.Rules(
        inside=(_cmd(Light.day_bulb, m.OFF), _cmd(Light.night_bulb, m.OFF)),
        present=(_cmd(Chromecast.cc, m.STOP),),
        absent=(_cmd(Chromecast.cc, m.RESUME),),
        shutdown=(_cmd(Light.lamp, m.OFF),),
    )
    return SimpleNamespace(
        setting=entrance_sensor.Settings(
            cleanup_delay_minutes=2,
            entrance=Sensor.entrance,
            patio_door=Sensor.patio,
            active_event="active",
            inactive_event="inactive",
            snapshot=45,
            listener="rex",
        ),
        message=entrance_sensor.Messages(
            log_present="skip (present)",
            log_door_open="skip (door open)",
            log_absent="skip (listener)",
            log_shutdown="applying OFF",
        ),
        rules=rules,
        timed=timed,
    )


@pytest.fixture
def plugin_ctx(ctx):
    ctx.api.check_presence.return_value = set()
    return ctx


def _cleanup(sensor, plugin_ctx):
    entry = m.LogEntry(_DAYTIME, plugins.Log.ENTRANCE, "Entrance sensor triggered")
    plugins._run_trigger_sensor_off.__wrapped__(sensor, entry, ctx=plugin_ctx)
    return entry


def _trigger_sensor(ctx, sensor, device_id, event):
    old = "inactive" if event == "active" else "active"
    name = "front door motion sensor" if device_id == "16" else f"device {device_id}"
    device = m.DeviceState(id=int(device_id), name=name, attributes={"motion": event}, last_activity=None)
    plugins._on_sensor_event(ctx, sensor, device, "motion", old, event)
    queued = [c for c in ctx.scheduler.add_job.call_args_list if c.args[0] is plugins._run_motion]
    ctx.scheduler.add_job.reset_mock()
    for call in queued:
        plugins._run_motion.__wrapped__(*call.kwargs["args"], ctx=ctx)


# --- Walking in ---


def test_day_walk_in_brightens_entrance(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    _trigger_sensor(ctx, sensor, "16", "active")
    ctx.api.dispatch.assert_called_once_with(m.squish((engine.Command(m.Devices(Light.day_bulb), 20),)), force=True, entry=ANY)


def test_night_walk_in_dims_entrance_and_stops_media(ctx, sensor):
    ctx.api.local_now.return_value = _NIGHTTIME
    _trigger_sensor(ctx, sensor, "16", "active")
    ctx.api.dispatch.assert_called_once_with(
        m.squish((engine.Command(m.Devices(Light.night_bulb), 1), engine.Command(m.Devices(Chromecast.cc), m.STOP))),
        force=True,
        entry=ANY,
    )


def test_walk_in_uses_first_window_that_contains_now(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    sensor.timed = {
        "Afternoon": [_window(time(14), time(16), _cmd(Light.night_bulb, 50))],
        **sensor.timed,
    }
    _trigger_sensor(ctx, sensor, "16", "active")
    executed = ctx.api.dispatch.call_args[0][0]
    assert engine.Command(m.Devices(Light.night_bulb), 50) in executed
    assert engine.Command(m.Devices(Light.day_bulb), 20) not in executed


def test_walk_in_outside_any_window_dispatches_nothing(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    sensor.timed = {"Morning": [_window(time(8), time(9), _cmd(Light.day_bulb, 20))]}
    _trigger_sensor(ctx, sensor, "16", "active")
    ctx.api.dispatch.assert_called_once_with(m.squish(()), force=True, entry=ANY)


def test_walk_in_shortly_after_shutdown_restores_house_lights(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    snap = _snapshot(
        engine.Command(m.Devices(Light.saved), m.ON),  # how the house looked before shutdown
        engine.Command(m.Devices(Light.day_bulb), m.OFF),  # entrance lights: off because the plugin turned them off
        engine.Command(m.Devices(Light.night_bulb), m.OFF),
    )
    ctx.engine.pop_snapshot.return_value = snap
    _trigger_sensor(ctx, sensor, "16", "active")
    executed = ctx.api.dispatch.call_args[0][0]
    assert {c.channel.one(): c.value for c in executed} == {
        Light.saved: m.ON,  # restored
        Light.day_bulb: 20,  # follows the current window, never the snapshot
    }
    ctx.engine.pop_snapshot.assert_called_once()


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


def test_motion_groups_under_the_trigger_entry(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    entry = m.LogEntry(_DAYTIME, plugins.Log.ENTRANCE, plugins.TRIGGER_MSG)
    ctx.api.log.return_value = entry
    _trigger_sensor(ctx, sensor, "16", "active")
    ctx.api.log.assert_called_once_with(plugins.Log.ENTRANCE, plugins.TRIGGER_MSG, amend=True)
    assert [c.action for c in entry.children] == ["Applying `Day` rules"]


# --- Walking past, into the house ---


def test_entrance_lights_turn_off_behind_you(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    _trigger_sensor(ctx, sensor, "16", "inactive")
    ctx.api.dispatch.assert_called_once_with(
        (engine.Command(m.Devices(Light.day_bulb), m.OFF), engine.Command(m.Devices(Light.night_bulb), m.OFF)),
        entry=ANY,
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
    plugin_ctx.api.dispatch.assert_called_once_with((engine.Command(m.Devices(Chromecast.cc), m.STOP),), entry=ANY)
    assert [c.action for c in entry.children] == [sensor.message.log_present]


def test_listener_home_alone_keeps_media_playing(sensor, plugin_ctx):
    plugin_ctx.api.local_now.return_value = _DAYTIME
    plugin_ctx.api.check_presence.return_value = {"rex"}
    entry = _cleanup(sensor, plugin_ctx)
    plugin_ctx.api.dispatch.assert_called_once_with((engine.Command(m.Devices(Chromecast.cc), m.RESUME),), entry=ANY)
    assert [c.action for c in entry.children] == [sensor.message.log_absent]


def test_listener_home_alone_restores_pre_visit_state(sensor, plugin_ctx):
    # An undetected visitor left: put the lights back how the dog had them
    plugin_ctx.api.local_now.return_value = _DAYTIME
    plugin_ctx.api.check_presence.return_value = {"rex"}
    entry = _cleanup(sensor, plugin_ctx)
    plugin_ctx.engine.restore_scene.assert_called_once_with(plugin_ctx, plugins.SNAPSHOT_NAME, (), entry)


def test_people_home_win_over_the_listener(sensor, plugin_ctx):
    plugin_ctx.api.local_now.return_value = _DAYTIME
    plugin_ctx.api.check_presence.return_value = {"alice", "rex"}
    entry = _cleanup(sensor, plugin_ctx)
    assert [c.action for c in entry.children] == [sensor.message.log_present]


def test_empty_quiet_house_shuts_down_and_snapshots(sensor, plugin_ctx):
    plugin_ctx.api.local_now.return_value = _DAYTIME
    entry = _cleanup(sensor, plugin_ctx)
    plugin_ctx.engine.override_scene.assert_called_once_with(
        plugin_ctx,
        plugins.SNAPSHOT_NAME,
        (engine.Command(m.Devices(Light.lamp), m.OFF),),
        _DAYTIME + timedelta(minutes=45),
        plugins.SNAPSHOT_NAME,
        entry,
    )
    plugin_ctx.api.dispatch.assert_not_called()
    assert [c.action for c in entry.children] == [sensor.message.log_shutdown]


def _seed_devices(plugin_ctx, *devices):
    plugin_ctx.api.device_states.return_value = list(devices)


def _door(state):
    return m.DeviceState(id=56, name="balcony door", attributes={"contact": state, "battery": "98"}, last_activity=None)


@pytest.mark.parametrize(
    "contact, expected",
    [
        ("open", "log_door_open"),
        ("closed", "log_shutdown"),
        (None, "log_shutdown"),
    ],
)
def test_door_state_drives_cleanup(sensor, plugin_ctx, contact, expected):
    plugin_ctx.api.local_now.return_value = _DAYTIME
    _seed_devices(plugin_ctx, *([_door(contact)] if contact else []))
    entry = _cleanup(sensor, plugin_ctx)
    assert [c.action for c in entry.children] == [getattr(sensor.message, expected)]


def _device(id=16, name="front door motion sensor", battery="100", attributes=None):
    return m.DeviceState(id=id, name=name, attributes=attributes or {"battery": battery}, last_activity=None)


def test_critical_battery_report_logs(plugin_ctx, sensor):
    plugin_ctx.api.local_now.return_value = _DAYTIME
    plugins._on_sensor_event(plugin_ctx, sensor, _device(battery="5"), "battery", "5", "5")
    plugin_ctx.api.log.assert_called_once_with(plugins.Log.ENTRANCE, "Low battery on `front door motion sensor` (CRITICAL)")


def test_healthy_battery_report_does_not_log(plugin_ctx, sensor):
    plugins._on_sensor_event(plugin_ctx, sensor, _device(battery="80"), "battery", None, "80")
    plugin_ctx.api.log.assert_not_called()


def test_unwatched_device_is_ignored(plugin_ctx, sensor):
    plugins._on_sensor_event(plugin_ctx, sensor, _device(id=99, name="other", battery="5"), "battery", None, "5")
    plugin_ctx.api.log.assert_not_called()


def test_battery_state_reads_the_device_cache(plugin_ctx, sensor):
    _seed_devices(plugin_ctx, _device(battery="80"))
    assert plugins.battery_state(plugin_ctx, sensor) == [
        m.DeviceStatus(name="front door motion sensor", details={"battery": "HIGH", "last_activity": None}),
        m.DeviceStatus(name="patio", details={"battery": None, "last_activity": None}),
    ]


def test_battery_state_lists_sensors_missing_from_the_cache(plugin_ctx, sensor):
    _seed_devices(plugin_ctx)
    assert plugins.battery_state(plugin_ctx, sensor) == [
        m.DeviceStatus(name="entrance", details={"battery": None, "last_activity": None}),
        m.DeviceStatus(name="patio", details={"battery": None, "last_activity": None}),
    ]


def test_setup_registers_listener_and_bound_provider(plugin_ctx, sensor):
    sensor.rules = {trigger: [commands] for trigger, commands in sensor.rules._asdict().items()}
    plugin_ctx.config.people = {"rex": []}
    plugin_ctx.config.ble_tags = {}
    with patch.object(entrance_sensor, "load_plugin_config", return_value=sensor):
        entrance_sensor.setup(plugin_ctx)
    plugin_ctx.api.add_listener.assert_called_once()
    title, provider = plugin_ctx.api.add_state_provider.call_args[0]
    assert title == "Entrance Sensors"
    _seed_devices(plugin_ctx, _device(battery="80"))
    assert provider() == [
        m.DeviceStatus(name="front door motion sensor", details={"battery": "HIGH", "last_activity": None}),
        m.DeviceStatus(name="patio", details={"battery": None, "last_activity": None}),
    ]


def _motion(ctx, sensor, old, new):
    device = _device(attributes={"motion": new})
    plugins._on_sensor_event(ctx, sensor, device, "motion", old, new)


def test_motion_republish_does_not_fire(ctx, sensor):
    _motion(ctx, sensor, "active", "active")
    ctx.api.dispatch.assert_not_called()


def test_cleanup_checks_presence_then_resumes(sensor, plugin_ctx):
    plugin_ctx.api.local_now.return_value = _DAYTIME
    _cleanup(sensor, plugin_ctx)
    plugin_ctx.api.check_presence.assert_called_once_with()
    plugin_ctx.api.resume_presence.assert_called_once_with()


def test_walk_out_pauses_presence(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    _trigger_sensor(ctx, sensor, "16", "inactive")
    ctx.api.pause_presence.assert_called_once_with()


def test_walk_in_resumes_presence(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    _trigger_sensor(ctx, sensor, "16", "active")
    ctx.api.resume_presence.assert_called_once_with()


# --- Guards ---


def test_other_devices_are_ignored(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    _trigger_sensor(ctx, sensor, "99", "active")
    ctx.api.dispatch.assert_not_called()
    ctx.engine.pop_snapshot.assert_not_called()


def test_unknown_events_are_ignored(ctx, sensor):
    ctx.api.local_now.return_value = _DAYTIME
    _trigger_sensor(ctx, sensor, "16", "other")
    ctx.api.dispatch.assert_not_called()
    ctx.scheduler.add_job.assert_not_called()
    ctx.engine.pop_snapshot.assert_not_called()
