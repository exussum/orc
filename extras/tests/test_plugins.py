from datetime import UTC, datetime, time, timedelta
from unittest.mock import ANY

import pytest
from orc_engine import engine
from orc_engine import model as em
from orc_extras import entrance_sensor
from orc_extras.entrance_sensor import plugins

import orc
from orc import model as m
from orc.dal.mqtt import hubitat
from orc.model import DeviceEnum
from tests.conftest import NOW, plugin_config

_NIGHTTIME = datetime(2024, 1, 1, 3, tzinfo=UTC)
_FUTURE = datetime(2100, 1, 1, tzinfo=UTC)


class Light(DeviceEnum):
    day_bulb = "1"
    night_bulb = "2"
    lamp = "3"
    saved = "4"


class Chromecast(DeviceEnum):
    cc = 1


class Sensor(DeviceEnum):
    entrance = "16"
    patio = "56"


DEVICE_ENUMS = {"Light": Light, "Chromecast": Chromecast, "Sensor": Sensor}


def _cmd(device, state):
    return em.Command[str, m.Devices](m.Devices(device), state)


def _window(name, start, stop, *commands):
    return em.Rule(tuple(em.Step(em.During(start, stop), c) for c in commands), name=name)


def _snapshot(*commands, end=_FUTURE):
    return m.SnapShot(routine=tuple(commands), end=end)


def _no_world(subject):
    raise KeyError(subject)


@pytest.fixture
def ctx(ctx, freezegun_clock):
    ctx.engine.evaluate.side_effect = engine.Runtime(UTC).evaluate
    ctx.api.reader.return_value = _no_world
    ctx.api.present_names.return_value = set()
    ctx.config.registry = orc.config.registry
    ctx.config.people = {"Rex": []}
    ctx.config.ble_tags = {}
    ctx.config.plugin_configs = plugin_config(entrance_sensor, "entrance_sensor")
    ctx.config.ad_hoc_routines = {
        "Lights Off": m.AdhocAction((_cmd(Light.day_bulb, m.OFF), _cmd(Light.night_bulb, m.OFF)), reset=False),
        "Lamp Off": m.AdhocAction((_cmd(Light.lamp, m.OFF),), reset=False),
        "Silence": m.AdhocAction((_cmd(Chromecast.cc, m.STOP),), reset=False),
        "Dog": m.AdhocAction((_cmd(Chromecast.cc, m.RESUME),), reset=False),
        "Day Scene": m.AdhocAction((_cmd(Light.day_bulb, 20),), reset=False),
        "Night Scene": m.AdhocAction((_cmd(Light.night_bulb, 1), _cmd(Chromecast.cc, m.STOP)), reset=False),
    }
    return ctx


@pytest.fixture
def config_sensor(ctx):
    entrance_sensor.setup(ctx)
    return ctx.api.add_listener.call_args.args[0].args[1]


@pytest.fixture
def nobody_home_ctx(ctx):
    ctx.api.check_presence.return_value = set()
    return ctx


def _cleanup(config_sensor, nobody_home_ctx, present_before=()):
    entry = m.LogEntry(NOW, plugins.Log.ENTRANCE, "Entrance sensor triggered", m.Manual("test"))
    nobody_home_ctx.plugin_state = {entrance_sensor: plugins.Visit(entry, set(present_before))}
    plugins._run_trigger_sensor_off.__wrapped__(config_sensor, entry, ctx=nobody_home_ctx)
    return entry


def _trigger_sensor(ctx, config_sensor, device_id, event):
    old = "inactive" if event == "active" else "active"
    name = "front door motion sensor" if device_id == "16" else f"device {device_id}"
    plugins._on_event(
        ctx, config_sensor, m.Status(m.Device(device_id, name, "hubitat"), "motion", old, event, hubitat.HubitatSource.HUBITAT)
    )
    queued = [c for c in ctx.scheduler.now.call_args_list if c.args[0] is plugins._run_motion]
    ctx.scheduler.now.reset_mock()
    for call in queued:
        plugins._run_motion.__wrapped__(*call.args[1:], ctx=ctx)


# --- Walking in ---


def test_day_walk_in_brightens_entrance(ctx, config_sensor):
    _trigger_sensor(ctx, config_sensor, "16", "active")
    ctx.api.dispatch.assert_called_once_with(m.squish((em.Command(m.Devices(Light.day_bulb), 20),)), force=True, entry=ANY)


def test_night_walk_in_dims_entrance_and_stops_media(ctx, config_sensor):
    ctx.frozen.move_to(_NIGHTTIME)
    _trigger_sensor(ctx, config_sensor, "16", "active")
    ctx.api.dispatch.assert_called_once_with(
        m.squish((em.Command(m.Devices(Light.night_bulb), 1), em.Command(m.Devices(Chromecast.cc), m.STOP))),
        force=True,
        entry=ANY,
    )


def test_walk_in_uses_first_window_that_contains_now(ctx, config_sensor):
    config_sensor.timed = (_window("Afternoon", time(14), time(16), _cmd(Light.night_bulb, 50)), *config_sensor.timed)
    _trigger_sensor(ctx, config_sensor, "16", "active")
    executed = ctx.api.dispatch.call_args[0][0]
    assert em.Command(m.Devices(Light.night_bulb), 50) in executed
    assert em.Command(m.Devices(Light.day_bulb), 20) not in executed


def test_walk_in_outside_any_window_dispatches_nothing(ctx, config_sensor):
    config_sensor.timed = (_window("Morning", time(8), time(9), _cmd(Light.day_bulb, 20)),)
    _trigger_sensor(ctx, config_sensor, "16", "active")
    ctx.api.dispatch.assert_called_once_with(m.squish(()), force=True, entry=ANY)


def test_walk_in_shortly_after_shutdown_restores_house_lights(ctx, config_sensor):
    snap = _snapshot(
        em.Command(m.Devices(Light.saved), m.ON),  # how the house looked before shutdown
        em.Command(m.Devices(Light.day_bulb), m.OFF),  # entrance lights: off because the plugin turned them off
        em.Command(m.Devices(Light.night_bulb), m.OFF),
    )
    ctx.engine.pop_snapshot.return_value = snap
    _trigger_sensor(ctx, config_sensor, "16", "active")
    executed = ctx.api.dispatch.call_args[0][0]
    assert {c.subject.one(): c.value for c in executed} == {
        Light.saved: m.ON,  # restored
        Light.day_bulb: 20,  # follows the current window, never the snapshot
    }
    ctx.engine.pop_snapshot.assert_called_once()


def test_walk_in_cancels_pending_cleanup(ctx, config_sensor):
    _trigger_sensor(ctx, config_sensor, "16", "active")
    ctx.scheduler.cancel.assert_called_once_with("trigger-sensor")


def test_before_set_is_captured_once(ctx, config_sensor):
    ctx.api.present_names.return_value = {"Rex"}
    ctx.plugin_state = {entrance_sensor: None}
    _trigger_sensor(ctx, config_sensor, "16", "active")
    ctx.api.present_names.return_value = set()
    _trigger_sensor(ctx, config_sensor, "16", "inactive")
    _trigger_sensor(ctx, config_sensor, "16", "active")
    assert ctx.plugin_state[entrance_sensor].present_before == {"Rex"}


def test_motion_groups_under_the_trigger_entry(ctx, config_sensor):
    entry = m.LogEntry(NOW, plugins.Log.ENTRANCE, plugins.TRIGGER_MSG, m.Manual("test"))
    ctx.api.log.return_value = entry
    _trigger_sensor(ctx, config_sensor, "16", "active")
    ctx.api.log.assert_called_once_with(plugins.Log.ENTRANCE, plugins.TRIGGER_MSG, m.Broker(id="16", source="hubitat"))
    assert [c.action for c in entry.children] == ["Applying `Day` rules"]


# --- Walking past, into the house ---


def test_entrance_lights_turn_off_behind_you(ctx, config_sensor):
    _trigger_sensor(ctx, config_sensor, "16", "inactive")
    ctx.api.run_action.assert_called_once_with(ctx, "Lights Off", ctx.api.log.return_value.trigger, source=plugins.Log.ENTRANCE)


def test_cleanup_is_scheduled_for_later(ctx, config_sensor):
    _trigger_sensor(ctx, config_sensor, "16", "inactive")
    ctx.scheduler.once.assert_called_once()
    _, kwargs = ctx.scheduler.once.call_args
    assert kwargs["id"] == "trigger-sensor"


# --- Cleanup, minutes later ---


def test_someone_home_stops_media(config_sensor, nobody_home_ctx):
    nobody_home_ctx.api.check_presence.return_value = {"alice"}
    entry = _cleanup(config_sensor, nobody_home_ctx)
    nobody_home_ctx.api.run_action.assert_called_once_with(nobody_home_ctx, "Silence", entry.trigger, source=plugins.Log.ENTRANCE)
    assert [c.action for c in entry.children] == [config_sensor.message.log_present]


def test_listener_home_alone_keeps_media_playing(config_sensor, nobody_home_ctx):
    nobody_home_ctx.api.check_presence.return_value = {"Rex"}
    entry = _cleanup(config_sensor, nobody_home_ctx)
    nobody_home_ctx.api.run_action.assert_called_once_with(nobody_home_ctx, "Dog", entry.trigger, source=plugins.Log.ENTRANCE)
    assert [c.action for c in entry.children] == [config_sensor.message.log_absent]


def test_listener_home_alone_restores_pre_visit_state(config_sensor, nobody_home_ctx):
    # An undetected visitor left: put the lights back how the dog had them
    nobody_home_ctx.api.check_presence.return_value = {"Rex"}
    entry = _cleanup(config_sensor, nobody_home_ctx)
    nobody_home_ctx.api.restore_scene.assert_called_once_with(plugins.SNAPSHOT_NAME, (), entry)


def test_people_home_win_over_the_listener(config_sensor, nobody_home_ctx):
    nobody_home_ctx.api.check_presence.return_value = {"alice", "Rex"}
    entry = _cleanup(config_sensor, nobody_home_ctx)
    assert [c.action for c in entry.children] == [config_sensor.message.log_present]


def test_empty_house_shuts_down_snapshots_and_pushes(config_sensor, nobody_home_ctx):
    entry = _cleanup(config_sensor, nobody_home_ctx)
    nobody_home_ctx.api.override_scene.assert_called_once_with(
        plugins.SNAPSHOT_NAME,
        (em.Command(m.Devices(Light.lamp), m.OFF),),
        NOW + timedelta(minutes=45),
        plugins.SNAPSHOT_NAME,
        entry,
    )
    nobody_home_ctx.api.run_action.assert_not_called()
    assert [c.action for c in entry.children] == [config_sensor.message.log_shutdown]
    nobody_home_ctx.api.log.assert_called_once_with(
        plugins.Log.ENTRANCE, config_sensor.message.log_nobody, entry.trigger, notification=m.Notification(("entrance",), "/presence/")
    )


def test_a_shutdown_after_a_tracked_person_left_is_not_pushed(config_sensor, nobody_home_ctx):
    entry = _cleanup(config_sensor, nobody_home_ctx, present_before={"Rex"})
    assert [c.action for c in entry.children] == [config_sensor.message.log_shutdown]
    nobody_home_ctx.api.log.assert_not_called()


def _seed_devices(nobody_home_ctx, *devices):
    nobody_home_ctx.api.device_states.return_value = list(devices)


def _door(state):
    return m.DeviceState(m.Device("56", "balcony door", "hubitat"), {"contact": state, "battery": "98"}, None)


@pytest.mark.parametrize(
    "contact, expected",
    [
        ("open", "log_door_open"),
        ("closed", "log_shutdown"),
        (None, "log_shutdown"),
    ],
)
def test_door_state_drives_cleanup(config_sensor, nobody_home_ctx, contact, expected):
    _seed_devices(nobody_home_ctx, *([_door(contact)] if contact else []))
    entry = _cleanup(config_sensor, nobody_home_ctx)
    assert [c.action for c in entry.children] == [getattr(config_sensor.message, expected)]


def test_setup_compiles_windows_and_registers_hooks(ctx, config_sensor):
    assert config_sensor.timed == (
        _window("Day", time(8), time(22), _cmd(Light.day_bulb, 20)),
        _window("Night", time(22), time(8), _cmd(Light.night_bulb, 1), _cmd(Chromecast.cc, m.STOP)),
    )
    ctx.api.add_listener.assert_called_once()


def test_cleanup_checks_presence_then_resumes(config_sensor, nobody_home_ctx):
    entry = _cleanup(config_sensor, nobody_home_ctx)
    nobody_home_ctx.api.check_presence.assert_called_once_with(entry.trigger, probe=True)
    nobody_home_ctx.api.resume_presence.assert_called_once_with(entry.trigger)


def test_walk_in_pauses_and_purges_presence(ctx, config_sensor):
    _trigger_sensor(ctx, config_sensor, "16", "active")
    ctx.api.pause_presence.assert_called_once_with()
    ctx.api.delete_all_presence.assert_called_once_with(ctx.api.log.return_value.trigger)


def test_walk_out_leaves_presence_alone(ctx, config_sensor):
    _trigger_sensor(ctx, config_sensor, "16", "inactive")
    ctx.api.pause_presence.assert_not_called()
    ctx.api.delete_all_presence.assert_not_called()


# --- Guards ---


def test_other_devices_are_ignored(ctx, config_sensor):
    _trigger_sensor(ctx, config_sensor, "99", "active")
    ctx.api.dispatch.assert_not_called()
    ctx.engine.pop_snapshot.assert_not_called()


def test_unknown_events_are_ignored(ctx, config_sensor):
    _trigger_sensor(ctx, config_sensor, "16", "other")
    ctx.api.dispatch.assert_not_called()
    ctx.scheduler.now.assert_not_called()
    ctx.engine.pop_snapshot.assert_not_called()
