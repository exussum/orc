from dataclasses import replace
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, call, create_autospec, patch
from uuid import UUID

import orc_queue as oq
import pytest
import time_machine
from orc_engine import model as em

import orc
from orc import api, config, plugins
from orc import model as m
from orc.dal import broker, net, push, sqlite
from orc.dal.findhub import stub as findhub_stub
from orc.dal.scheduler import Scheduler
from orc.interfaces import inbound, runner
from orc.log import Log
from orc.model import DeviceEnum

FUTURE = datetime(2100, 1, 1, tzinfo=config.settings.tz)
EXTERNAL_EVENT = m.Broker("external", origin="hubitat", log_source=m.LogSource.EXTERNAL)
NOTIFY = m.Manual("notify", log_source=m.LogSource.SYSTEM)
TRIGGER = m.Query("test", log_source=m.LogSource.SYSTEM)
PAST = datetime(2000, 1, 1, tzinfo=config.settings.tz)
X, Y = UUID(int=1), UUID(int=2)


class Light(DeviceEnum):
    a = 1
    b = 2
    c = 3


class Chromecast(DeviceEnum):
    x = 1


Light._sort = 0
Chromecast._sort = 1


def _cmd(what, value):
    return em.Command(m.Devices(what), value)


@pytest.fixture
def snapshot_commands():
    return (em.Command(m.Devices(orc.Light.a), m.ON), em.Command(m.Devices(orc.Light.b), m.OFF))


@pytest.fixture
def elapsed_schedule():
    def install(rule, id):
        past = datetime(2026, 1, 5, 8, tzinfo=config.settings.tz)
        return patch.object(api, "get_schedule", return_value=[(past, m.ThemeEntry(time(8, 0), rule, id))])

    return install


@pytest.fixture
def system_snapshot(snapshot_commands):
    api._ctx.engine.save_snapshot(api.ORC_SYSTEM_SNAPSHOT, m.SnapShot(routine=snapshot_commands, end=FUTURE))


@pytest.fixture
def log_entry():
    return api.context().log.write("test", replace(TRIGGER, log_source=m.LogSource.MANUAL))


@pytest.fixture
def push_subscriptions():
    def build(names):
        return [m.PushSubscription(f"https://push.example/{name}", "public-key", "auth-secret") for name in names]

    return build


class TestRestoreScene:
    def test_restore_with_snapshot(self, command_dispatch, snapshot_commands, log_entry):
        api._ctx.engine.save_snapshot("test", m.SnapShot(routine=snapshot_commands, end=FUTURE))
        api.restore_scene("test", (), log_entry.trigger)
        assert command_dispatch.call_args_list == [call(snapshot_commands, force=True, trigger=log_entry.trigger)]

    def test_restore_without_snapshot(self, command_dispatch, snapshot_commands, log_entry):
        api.restore_scene("test", snapshot_commands, log_entry.trigger)
        assert command_dispatch.call_args_list == [call(snapshot_commands, force=True, trigger=log_entry.trigger)]

    def test_restore_drops_expired_snapshot(self, command_dispatch, snapshot_commands, log_entry):
        api._ctx.engine.save_snapshot("test", m.SnapShot(routine=snapshot_commands, end=PAST))
        api.restore_scene("test", snapshot_commands, log_entry.trigger)
        assert command_dispatch.call_args_list == [call(snapshot_commands, force=True, trigger=log_entry.trigger)]
        assert not api._ctx.engine.snapshots()


@patch("orc.dal.broker.stub.command")
class TestIntercepts:
    def test_system_command_overwrites_snapshot(self, update_light, system_snapshot, log_entry):
        command = em.Command(m.Devices(orc.Light.b), m.ON, tag=em.Always())

        api.dispatch((command,), trigger=log_entry.trigger)
        api.dispatch((command,), trigger=log_entry.trigger)

        assert api._ctx.engine.snapshots()[api.ORC_SYSTEM_SNAPSHOT].routine == (
            em.Command(m.Devices(orc.Light.a), m.ON),
            em.Command(m.Devices(orc.Light.b), m.ON, tag=em.Always()),
        )
        assert update_light.call_args_list == [call(orc.Light.b, m.ON), call(orc.Light.b, m.ON)]

    def test_snapshot_update_add(self, update_light, system_snapshot, log_entry):
        command = em.Command(m.Devices(orc.Light.c), m.ON, tag=em.Always())

        api.dispatch((command,), trigger=log_entry.trigger)

        assert api._ctx.engine.snapshots()[api.ORC_SYSTEM_SNAPSHOT].routine == (
            em.Command(m.Devices(orc.Light.a), m.ON),
            em.Command(m.Devices(orc.Light.b), m.OFF),
            command,
        )
        assert update_light.call_args_list == [call(orc.Light.c, m.ON)]

    def test_untagged_command_suppressed(self, update_light, system_snapshot, log_entry):
        command = em.Command(m.Devices(orc.Light.c), m.ON)

        api.dispatch((command,), trigger=log_entry.trigger)

        assert api._ctx.engine.snapshots()[api.ORC_SYSTEM_SNAPSHOT].routine == (
            em.Command(m.Devices(orc.Light.a), m.ON),
            em.Command(m.Devices(orc.Light.b), m.OFF),
        )
        assert update_light.call_args_list == []

    def test_expired_snapshot_drops(self, update_light, snapshot_commands, log_entry):
        command = em.Command(m.Devices(orc.Light.c), m.ON)

        api._ctx.engine.save_snapshot(api.ORC_SYSTEM_SNAPSHOT, m.SnapShot(routine=snapshot_commands, end=PAST))
        api.dispatch((command,), trigger=log_entry.trigger)

        assert not api._ctx.engine.snapshots()
        assert update_light.call_args_list == [call(orc.Light.c, m.ON)]

    def test_unrelated_plugin_snapshot_does_not_suppress(self, update_light, snapshot_commands, log_entry):
        command = em.Command(m.Devices(orc.Light.c), m.ON)

        api._ctx.engine.save_snapshot("entrance_sensor", m.SnapShot(routine=snapshot_commands, end=FUTURE))
        api.dispatch((command,), trigger=log_entry.trigger)

        assert update_light.call_args_list == [call(orc.Light.c, m.ON)]

    def test_snapshot_bypassed(self, update_light, system_snapshot, log_entry):
        command = em.Command(m.Devices(orc.Light.c), m.ON)

        api.dispatch((command,), force=True, trigger=log_entry.trigger)

        assert api._ctx.engine.snapshots()[api.ORC_SYSTEM_SNAPSHOT].routine == (
            em.Command(m.Devices(orc.Light.a), m.ON),
            em.Command(m.Devices(orc.Light.b), m.OFF),
        )
        assert update_light.call_args_list == [call(orc.Light.c, m.ON)]

    def test_force_off_is_not_recorded_and_resume_relights(self, update_light, system_snapshot, log_entry):
        api.dispatch((em.Command(m.Devices(orc.Light.a), m.OFF),), force=True, trigger=log_entry.trigger)

        assert api._ctx.engine.snapshots()[api.ORC_SYSTEM_SNAPSHOT].routine == (
            em.Command(m.Devices(orc.Light.a), m.ON),
            em.Command(m.Devices(orc.Light.b), m.OFF),
        )

        api.restore_scene(api.ORC_SYSTEM_SNAPSHOT, (), log_entry.trigger)

        assert update_light.call_args_list == [
            call(orc.Light.a, m.OFF),  # the deliberate off
            call(orc.Light.a, m.ON),  # resume undoes it
            call(orc.Light.b, m.OFF),
        ]


def test_dispatch_usb_sets_volume(log_entry):
    from orc.dal.audio import stub as audio_stub

    api.dispatch((em.Command(m.Devices(orc.USB.speaker), 50),), force=True, trigger=log_entry.trigger)

    assert audio_stub._volumes == {orc.USB.speaker: 50}


def test_dispatch_usb_plays_alert_path(log_entry):
    from orc.dal.audio import stub as audio_stub

    api.dispatch((em.Command(m.Devices(orc.USB.speaker), "/tmp/alert.wav"),), force=True, trigger=log_entry.trigger)

    assert audio_stub._alerted == ["/tmp/alert.wav"]


def test_back_on_schedule_checks_presence_then_replays(log_entry):
    ctx = MagicMock()
    ctx.api = create_autospec(api)
    plugins.back_on_schedule(ctx, None, trigger=log_entry.trigger)
    ctx.api.check_presence.assert_called_once_with(log_entry.trigger)
    ctx.api.replay_day.assert_called_once_with(ctx.api.local_now.return_value, log_entry.trigger)


def test_button_ad_hoc_snapshot(ctx, config_ad_hoc, command_dispatch):
    action = m.AdhocAction((em.Command(m.Devices(orc.Light.b), m.ON),), snapshot=timedelta(hours=3))
    captured = (em.Command(m.Devices(orc.Light.a), m.ON),)
    with (
        config_ad_hoc({"r": action}),
        patch.object(api, "capture_lights", return_value=captured),
        patch.object(api, "capture_sounds", return_value=()),
    ):
        api.run_action("r", m.Button("1", log_source=m.LogSource.EXTERNAL))
    snap = ctx.engine.snapshots()[api.ORC_SYSTEM_SNAPSHOT]
    assert snap.routine == captured
    assert snap.end > api.local_now()
    command_dispatch.assert_called_once_with(action.commands, force=True, trigger=ANY)


def test_button_ad_hoc_snapshot_pauses_chromecasts_and_captures_them(ctx, config_ad_hoc, command_dispatch):
    cast = m.Devices(orc.Chromecast.x)
    action = m.AdhocAction((em.Command(m.Devices(orc.Light.b), m.ON), em.Command(cast, m.STOP)), snapshot=timedelta(hours=3))
    lights = (em.Command(m.Devices(orc.Light.a), m.ON),)
    devices = (em.Command(cast, 40), em.Command(cast, m.RESUME))
    with (
        config_ad_hoc({"r": action}),
        patch.object(api, "capture_lights", return_value=lights),
        patch.object(api, "capture_sounds", return_value=devices),
    ):
        api.run_action("r", m.Button("1", log_source=m.LogSource.EXTERNAL))
    assert ctx.engine.snapshots()[api.ORC_SYSTEM_SNAPSHOT].routine == (*lights, *devices)
    command_dispatch.assert_called_once_with((action.commands[0], em.Command(cast, m.PAUSE)), force=True, trigger=ANY)


def test_pause_not_stop_only_changes_chromecast_stop():
    cast, light = m.Devices(orc.Chromecast.x), m.Devices(orc.Light.a)
    commands = (em.Command(cast, m.STOP), em.Command(cast, 30), em.Command(light, m.OFF), em.Command(cast, m.RESUME))
    assert api._pause_not_stop(commands) == (em.Command(cast, m.PAUSE), *commands[1:])


def test_snapshot_counts_lights_on_and_sounds_resuming():
    cast = m.Devices(orc.Chromecast.x)
    commands = (
        em.Command(m.Devices(orc.Light.a), 40),
        em.Command(m.Devices(orc.Light.b), m.OFF),
        em.Command(m.Devices(orc.Light.c), m.ON),
        em.Command(cast, 30),
        em.Command(cast, m.RESUME),
    )
    assert api.snapshot_counts(commands) == (2, 1)


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (m.SoundState(what=None, content="song", volume=40, playback=m.Playback.PLAYING), (40, m.RESUME)),
        (m.SoundState(what=None, content="song", volume=40, playback=m.Playback.PAUSED), (40,)),
        (m.SoundState(what=None, content=None, volume=25, playback=m.Playback.STOPPED), (25,)),
        (m.SoundState(what=None, content=None, volume=0), ()),
    ],
)
def test_capture_sounds_records_volume_and_whether_a_chromecast_was_playing(state, expected):
    with patch.object(api, "sound_states", return_value=(replace(state, what=orc.Chromecast.x),)):
        captured = api.capture_sounds()
    assert captured == tuple(em.Command(m.Devices(orc.Chromecast.x), value) for value in expected)


def test_capture_sounds_leaves_out_usb_devices():
    usb = m.SoundState(what=orc.USB.speaker, content="song", volume=40, playback=m.Playback.PLAYING)
    with patch.object(api, "sound_states", return_value=(usb,)):
        assert api.capture_sounds() == ()


def test_sound_states_logs_and_skips_a_device_it_cannot_read(caplog):
    usb = m.SoundState(what=orc.USB.speaker, content="song", volume=40)

    def read(w):
        if w is orc.Chromecast.x:
            raise OSError("offline")
        return usb

    with patch.object(api, "_fetch_sound", side_effect=read):
        assert api.sound_states() == (usb,)
    assert f"could not read the state of {orc.Chromecast.x.name}" in caplog.text


def test_button_ad_hoc_snapshot_does_not_stack(ctx, config_ad_hoc, command_dispatch, light_capture):
    action = m.AdhocAction((em.Command(m.Devices(orc.Light.b), m.ON),), snapshot=timedelta(hours=3))
    reset = _routine("reset", "", em.Command(m.Devices(orc.Light.a), m.OFF))
    existing = (em.Command(m.Devices(orc.Light.a), m.ON),)
    snap = m.SnapShot(routine=existing, end=api.local_now() + timedelta(hours=1))
    ctx.engine.save_snapshot(api.ORC_SYSTEM_SNAPSHOT, snap)
    with config_ad_hoc({"r": action}, reset_config=reset):
        api.run_action("r", m.Button("1", log_source=m.LogSource.EXTERNAL))
    # Existing snapshot is preserved (not popped, not overwritten) and no new one is taken.
    assert ctx.engine.snapshots()[api.ORC_SYSTEM_SNAPSHOT].routine is existing
    light_capture.assert_not_called()
    command_dispatch.assert_called_once_with((*reset.commands, *action.commands), force=True, trigger=ANY)


def test_dispatch_routes_ac_commands_to_the_broker(log_entry):
    with patch.object(config.providers.broker, "command") as command:
        api.dispatch(
            (em.Command(m.Devices(orc.AC.unit), m.AcState(m.ON, m.AcMode.COOL, "low", temperature=75)),),
            force=True,
            trigger=log_entry.trigger,
        )
        api.dispatch((em.Command(m.Devices(orc.AC.unit), m.AcState(power=m.ON)),), force=True, trigger=log_entry.trigger)
        api.dispatch((em.Command(m.Devices(orc.AC.unit), m.AcState(power=m.OFF)),), force=True, trigger=log_entry.trigger)
    assert command.call_args_list == [
        call(orc.AC.unit, m.AcState(m.ON, m.AcMode.COOL, "low", temperature=75)),
        call(orc.AC.unit, m.AcState(power=m.ON, mode="cool")),
        call(orc.AC.unit, m.AcState(power=m.OFF)),
    ]


def test_capture_acs_reads_the_device_cache():
    assert api.capture_acs() == (m.AcStatus(orc.AC.unit, None),)
    with _ac(power="on", mode="cool", temperature=72.4):
        assert api.capture_acs() == (m.AcStatus(orc.AC.unit, m.AcState(power="on", mode="cool", temperature=72.4)),)
    with _ac(power="on", mode="heat", temperature=72):
        assert api.capture_acs() == (m.AcStatus(orc.AC.unit, m.AcState(power="on", mode="heat", temperature=72)),)


def test_ac_on_keeps_the_remembered_mode():
    with _ac(power="off", mode="dry", fan_mode="low", temperature=77):
        assert api.ac_command(orc.AC.unit, m.AcState(power=m.ON)) == m.AcState(m.ON, "dry", "low", temperature=77)


def test_ac_on_with_no_mode_cools():
    assert api.ac_command(orc.AC.unit, m.AcState(power=m.ON)) == m.AcState(m.ON, "cool")


def test_capture_acs_reads_an_off_unit():
    with _ac(power="off", mode="cool", temperature=72):
        assert api.capture_acs() == (m.AcStatus(orc.AC.unit, m.AcState(power="off", mode="cool", temperature=72)),)


@pytest.mark.parametrize(
    ("attributes", "expected"),
    [
        ({"switch": "on", "level": "50"}, 50),
        ({"switch": "on"}, "on"),
        ({"switch": "off", "level": "50"}, "off"),
        (None, "off"),
    ],
)
def test_capture_lights_lists_every_light(attributes, expected):
    states = [m.DeviceState(m.Device(orc.Light.a.value, "lamp", "hubitat"), attributes, None)] if attributes else []
    with patch.object(broker, "snapshot", return_value=states):
        captured = {m.Devices(c.subject).one(): c.value for c in api.capture_lights()}
    assert captured[orc.Light.a] == expected
    assert set(captured) == set(orc.Light)


def test_capture_sensors_reads_the_device_cache():
    device = m.DeviceState(m.Device(orc.Sensor.living.value, "living room sensor", "hubitat"), {"temperature": 70}, "2026-01-05 12:00:00")
    with patch.object(broker, "snapshot", return_value=[device]):
        assert api.capture_sensors() == [
            m.DeviceStatus(name="living room sensor", details={"temperature": 70, "last_activity": "2026-01-05 12:00:00"})
        ]


def test_capture_sensors_lists_sensors_missing_from_the_cache():
    assert api.capture_sensors() == [m.DeviceStatus(name=orc.Sensor.living.name, details={})]


def test_dispatch_failure_nests_under_the_entry(log_entry):
    with patch.object(config.providers.broker, "command", side_effect=RuntimeError("boom")):
        api.dispatch((em.Command(m.Devices(orc.Light.a), m.ON),), force=True, trigger=log_entry.trigger)
    assert log_entry.children[-1].action == "Dispatch failed for `a`: boom"


def test_dispatch_usb_speaks_state_error(log_entry):
    from orc.dal.audio import stub as audio_stub

    api.dispatch((em.Command(m.Devices(orc.USB.speaker), m.ON),), force=True, trigger=log_entry.trigger)

    assert len(audio_stub._spoken) == 1
    assert "USB devices don't support state" in audio_stub._spoken[0]


class TestRefreshTagKeys:
    @pytest.fixture(autouse=True)
    def reset_log(self):
        api.context().log.clear()

    def test_a_new_key_reaches_the_row_and_the_listener(self):
        with patch.object(net.presence, "set_key") as set_key:
            api.refresh_tag_keys(m.Manual("secrets", log_source=m.LogSource.SYSTEM))
        key = m.BleKey(b"Alice Tag".ljust(32, b"\0"), 0)
        assert sqlite.fetch_tags() == [m.Tag("Alice", key, None)]
        set_key.assert_called_once_with("Alice", key)
        assert [e.action for e in api.context().log.rows()] == [
            "Tag key for `Alice` read from Find Hub: e595fa93 paired 1969-12-31 19:00:00"
        ]

    def test_an_unchanged_key_is_silent(self):
        api.refresh_tag_keys(m.Manual("secrets", log_source=m.LogSource.SYSTEM))
        api.context().log.clear()
        api.refresh_tag_keys(m.Manual("secrets", log_source=m.LogSource.SYSTEM))
        assert api.context().log.rows() == []

    def test_a_moved_pairing_date_keeps_the_stored_key(self, monkeypatch):
        api.refresh_tag_keys(m.Manual("secrets", log_source=m.LogSource.SYSTEM))
        api.context().log.clear()
        key = m.BleKey(b"Alice Tag".ljust(32, b"\0"), 0)
        monkeypatch.setattr(findhub_stub, "identity_key", lambda device: m.BleKey(key.eik, 8293))
        api.refresh_tag_keys(m.Manual("secrets", log_source=m.LogSource.SYSTEM))
        assert sqlite.fetch_tags() == [m.Tag("Alice", key, None)]
        assert api.context().log.rows() == []

    def test_a_failed_lookup_logs_and_keeps_going(self, monkeypatch):
        monkeypatch.setattr(findhub_stub, "identity_key", MagicMock(side_effect=RuntimeError("boom")))
        api.refresh_tag_keys(m.Manual("secrets", log_source=m.LogSource.SYSTEM))
        assert sqlite.fetch_tags() == []
        assert [e.action for e in api.context().log.rows()] == ["Find Hub lookup failed for `Alice`: boom"]


class TestLog:
    @pytest.fixture(autouse=True)
    def reset_log(self):
        api.context().log.clear()

    def test_an_equal_trigger_within_the_window_nests(self):
        api.context().log.write("first", m.Integration("x", id=X, log_source=m.LogSource.PLUGIN))
        api.context().log.write("second", m.Integration("x", id=X, log_source=m.LogSource.PLUGIN))
        assert [(e.action, [c.action for c in e.children]) for e in api.context().log.rows()] == [("first", ["second"])]

    def test_an_equal_trigger_after_the_window_starts_its_own_entry(self):
        with time_machine.travel(datetime(2026, 1, 5, 12, tzinfo=config.settings.tz), tick=False) as frozen:
            api.context().log.write("first", m.Integration("x", id=X, log_source=m.LogSource.PLUGIN))
            frozen.shift(Log.ROLLUP_WINDOW)
            api.context().log.write("later", m.Integration("x", id=X, log_source=m.LogSource.PLUGIN))
        assert [(e.action, e.children) for e in api.context().log.rows()] == [("later", []), ("first", [])]

    def test_an_open_entry_takes_an_equal_trigger_until_it_closes(self):
        with time_machine.travel(datetime(2026, 1, 5, 12, tzinfo=config.settings.tz), tick=False) as frozen:
            entry = api.context().log.write("first", m.Integration("x", id=X, log_source=m.LogSource.SYSTEM))
            with api.context().log.opened(entry):
                frozen.shift(timedelta(hours=1))
                api.context().log.write("later", m.Integration("x", id=X, log_source=m.LogSource.PLUGIN))
                api.context().log.write("other", m.Integration("y", id=Y, log_source=m.LogSource.PLUGIN))
            frozen.shift(timedelta(hours=1))
            api.context().log.write("closed", m.Integration("x", id=X, log_source=m.LogSource.PLUGIN))
        assert [(e.action, [c.action for c in e.children]) for e in api.context().log.rows()] == [
            ("closed", []),
            ("other", []),
            ("first", ["later"]),
        ]

    def test_a_swarm_of_external_changes_rolls_up(self):
        with time_machine.travel(datetime(2026, 1, 5, 12, tzinfo=config.settings.tz), tick=False) as frozen:
            api.log_external(m.Status(m.Device("1", "lamp a", "hubitat"), "switch", "off", "on", m.Source.EXTERNAL), EXTERNAL_EVENT)
            api.log_external(m.Status(m.Device("2", "lamp b", "hubitat"), "switch", "off", "on", m.Source.EXTERNAL), EXTERNAL_EVENT)
            api.log_external(m.Status(m.Device("2", "lamp b", "hubitat"), "switch", "on", "off", m.Source.ORC), EXTERNAL_EVENT)
            frozen.shift(Log.ROLLUP_WINDOW)
            api.log_external(m.Status(m.Device("1", "lamp a", "hubitat"), "switch", "on", "off", m.Source.EXTERNAL), EXTERNAL_EVENT)
        assert [(e.action, [c.action for c in e.children]) for e in api.context().log.rows()] == [
            ("`lamp a` switch: on → off", []),
            ("`lamp a` switch: off → on", ["`lamp b` switch: off → on"]),
        ]

    def test_separate_messages_roll_up_by_name(self):
        def message(device, new):
            trigger = m.Broker(device, origin="hubitat", log_source=m.LogSource.EXTERNAL)
            api.log_external(m.Status(m.Device(device, f"lamp {device}", "hubitat"), "switch", "off", new, m.Source.EXTERNAL), trigger)

        with time_machine.travel(datetime(2026, 1, 5, 12, tzinfo=config.settings.tz), tick=False) as frozen:
            message("1", "on")
            message("2", "on")
            frozen.shift(Log.ROLLUP_WINDOW)
            message("3", "on")
        assert [(e.action, [c.action for c in e.children]) for e in api.context().log.rows()] == [
            ("`lamp 3` switch: off → on", []),
            ("`lamp 1` switch: off → on", ["`lamp 2` switch: off → on"]),
        ]

    def test_attach_joins_the_open_row_only(self):
        log = api.context().log
        entry = log.write("visit", m.Integration("visit", id=X, log_source=m.LogSource.SYSTEM))
        later = m.Integration("visit", id=Y, log_source=m.LogSource.PLUGIN)
        log.attach(entry, later)
        with time_machine.travel(datetime(2026, 1, 5, 12, tzinfo=config.settings.tz), tick=False) as frozen, log.opened(entry):
            frozen.shift(timedelta(hours=1))
            log.write("joined", later)
            log.write("stranger", m.Integration("visit", id=UUID(int=3), log_source=m.LogSource.PLUGIN))
        assert [(e.action, [c.action for c in e.children]) for e in log.rows()] == [("stranger", []), ("visit", ["joined"])]

    def test_a_different_trigger_starts_its_own_entry(self):
        api.context().log.write("first", m.Integration("x", id=X, log_source=m.LogSource.PLUGIN))
        api.context().log.write("other", m.Integration("y", id=Y, log_source=m.LogSource.PLUGIN))
        entries = api.context().log.rows()
        assert [e.action for e in entries] == ["other", "first"]
        assert entries[0].children == []

    def test_a_nested_line_still_notifies(self):
        entry = api.context().log.write("first", m.Integration("x", id=X, log_source=m.LogSource.PLUGIN))
        with time_machine.travel(datetime(2026, 1, 5, 12, tzinfo=config.settings.tz), tick=False), api.context().log.opened(entry):
            api.context().log.write(
                "later `x`", m.Integration("x", id=X, log_source=m.LogSource.PLUGIN), notification=m.Notification(("calendar", "dentist"))
            )
        assert api._ctx.scheduler.now.call_args.args[1] == (
            "[Plugin 01/05]",
            "later x",
            m.Notification(("calendar", "dentist")),
            m.Integration("x", id=X, log_source=m.LogSource.PLUGIN),
            None,
        )

    def test_a_link_rides_the_push_as_given(self):
        notification = m.Notification(("calendar",), "https://meet.google.com/abc?x=1")
        with time_machine.travel(datetime(2026, 1, 5, 12, tzinfo=config.settings.tz), tick=False):
            api.context().log.write("standup", m.Integration("x", id=X, log_source=m.LogSource.PLUGIN), notification=notification)
        assert api._ctx.scheduler.now.call_args.args[1][2] == notification

    def test_a_greeted_subscription_is_pushed_alone(self, push_provider, push_subscriptions):
        push_subscriptions = push_subscriptions("ab")
        api.subscribe_push(push_subscriptions[0], NOTIFY)
        with time_machine.travel(datetime(2026, 1, 5, 12, tzinfo=config.settings.tz), tick=False):
            api.subscribe_push(push_subscriptions[1], NOTIFY, greet=True)
        now = api._ctx.scheduler.now
        assert now.call_args.args[0] is api._push_job
        assert now.call_args.args[1] == (
            "[System 01/05]",
            "Notifications enabled on this device",
            m.Notification(("greeting",)),
            NOTIFY,
            (push_subscriptions[1],),
        )
        assert set(sqlite.fetch_push_subscriptions()) == set(push_subscriptions)

    def test_unsubscribing_drops_only_that_endpoint(self, push_subscriptions):
        push_subscriptions = push_subscriptions("ab")
        for subscription in push_subscriptions:
            api.subscribe_push(subscription, NOTIFY)
        api.unsubscribe_push(push_subscriptions[0].endpoint)
        assert sqlite.fetch_push_subscriptions() == [push_subscriptions[1]]

    def test_a_push_reaches_every_subscription_and_drops_gone_ones(self, push_provider, push_subscriptions):
        push_subscriptions = push_subscriptions("abc")
        for subscription in push_subscriptions:
            api.subscribe_push(subscription, NOTIFY)
        push_provider.send.side_effect = [None, push.Gone("b"), RuntimeError("boom")]
        entry = api.context().log.write(
            "Leak at `kitchen`", m.Integration("x", id=X, log_source=m.LogSource.PLUGIN), notification=m.Notification(("leak",))
        )
        api._push_job(("[Plugin 01/05]", "Leak at kitchen", "leak", entry.trigger, None), ctx=api._ctx)
        assert push_provider.send.call_args_list == [call(s, "[Plugin 01/05]", "Leak at kitchen", "leak") for s in push_subscriptions]
        assert set(sqlite.fetch_push_subscriptions()) == {push_subscriptions[0], push_subscriptions[2]}
        assert [c.action for c in entry.children] == ["Push failed for `…xample/c`: boom"]


@pytest.mark.time_machine(datetime(2026, 1, 5, 12, tzinfo=config.settings.tz), tick=False)
class TestActiveOverride:
    OVERRIDE = m.ThemeOverride("vacation", date(2026, 1, 1), date(2026, 1, 10))

    @pytest.fixture(autouse=True)
    def theme_override(self):
        with patch.dict(config.themes, {"vacation": m.Theme("vacation", ())}):
            api.set_theme_override(*self.OVERRIDE)
            yield

    def test_no_override(self):
        api.clear_theme_override()
        assert api.active_theme_override(date(2026, 1, 5)) is None

    def test_active_inside_window(self):
        assert api.active_theme_override(date(2026, 1, 5)) == self.OVERRIDE

    def test_active_on_start_boundary(self):
        assert api.active_theme_override(date(2026, 1, 1)) == self.OVERRIDE

    def test_active_on_end_boundary(self):
        assert api.active_theme_override(date(2026, 1, 10)) == self.OVERRIDE

    def test_inactive_before_window(self):
        assert api.active_theme_override(date(2025, 12, 31)) is None

    def test_inactive_after_window(self):
        assert api.active_theme_override(date(2026, 1, 11)) is None


@pytest.mark.time_machine(datetime(2026, 1, 5, 12, tzinfo=config.settings.tz), tick=False)
class TestWorkDayTheme:
    def test_a_weekday_that_the_market_trades_is_a_work_day(self):
        assert api.calculate_theme(date(2026, 1, 5), TRIGGER) == m.THEME_WORK_DAY

    def test_a_weekend_is_a_day_off(self):
        assert api.calculate_theme(date(2026, 1, 3), TRIGGER) == m.THEME_DAY_OFF

    def test_a_market_holiday_is_a_day_off(self):
        with patch.object(config.providers.holiday, "market_holiday", return_value=True):
            assert api.calculate_theme(date(2026, 1, 5), TRIGGER) == m.THEME_DAY_OFF

    @pytest.mark.parametrize("exc", [ConnectionError("down"), RuntimeError("feed error"), ValueError("bad json"), KeyError("date")])
    def test_a_failing_holiday_provider_means_a_work_day(self, exc):
        api.context().log.clear()
        with patch.object(config.providers.holiday, "market_holiday", side_effect=exc):
            assert api.calculate_theme(date(2026, 1, 5), TRIGGER) == m.THEME_WORK_DAY
        assert [(e.action, e.trigger) for e in api.context().log.rows()] == [
            (f"Market holiday check failed for 2026-01-05, treating it as a work day: {exc}", TRIGGER)
        ]

    def test_rebuild_logs_holiday_failure(self):
        api.context().log.clear()
        trigger = m.Manual("theme", log_source=m.LogSource.MANUAL)
        with patch.object(config.providers.holiday, "market_holiday", side_effect=ConnectionError("down")):
            api.rebuild_jobs(trigger)
        assert {e.trigger for e in api.context().log.rows()} == {trigger}

    def test_iot_cron_logs_holiday_failure(self):
        api.context().log.clear()
        with patch.object(config.providers.holiday, "market_holiday", side_effect=ConnectionError("down")):
            inbound.rebuild_iot_cron()
        ((kind, name),) = {(type(e.trigger), e.trigger.name) for e in api.context().log.rows()}
        assert (kind, name) == (m.Cron, "iot")

    def test_an_override_decides_it(self):
        api.set_theme_override("day off", date(2026, 1, 5), date(2026, 1, 5))
        assert api.calculate_theme(date(2026, 1, 5), TRIGGER) != m.THEME_WORK_DAY

    def test_a_weekday_named_theme_wins_over_the_base_theme(self):
        monday = m.Theme("monday", ())
        with patch.object(config, "themes", {**config.themes, "monday": monday}):
            assert api.calculate_theme(date(2026, 1, 5), TRIGGER) == "monday"


# 2026-01-03 is Saturday, 2026-01-04 is Sunday
@pytest.mark.time_machine(datetime(2026, 1, 3, 12, tzinfo=config.settings.tz), tick=False)
class TestGetSchedule:
    @staticmethod
    def _theme(name, *routine_names):
        return m.Theme(name, tuple(m.ThemeEntry(time(8, 0), _routine(n, time(8, 0)), n) for n in routine_names))

    @pytest.fixture(autouse=True)
    def themes(self):
        self.themes = {
            "saturday": self._theme("saturday", "sat-r"),
            "sunday": self._theme("sunday", "sun-r"),
            "work day": self._theme("work day", "work-r"),
            "day off": self._theme("day off", "off-r"),
        }
        with patch.object(config, "themes", self.themes):
            yield

    @staticmethod
    def _names(schedule):
        return [entry.routine.name for _, entry in schedule]

    def test_override_wins_over_weekday_named_theme(self):
        self.themes["vacation"] = self._theme("vacation", "vac-r")
        api.set_theme_override("vacation", date(2026, 1, 3), date(2026, 1, 4))
        assert self._names(api.get_schedule(TRIGGER)) == ["vac-r", "vac-r"]

    def test_empty_override_clears_weekday_named_theme(self):
        self.themes["empty"] = self._theme("empty")
        api.set_theme_override("empty", date(2026, 1, 3), date(2026, 1, 4))
        assert self._names(api.get_schedule(TRIGGER)) == []

    def test_weekday_named_theme_used_when_no_override(self):
        assert self._names(api.get_schedule(TRIGGER)) == ["sat-r", "sun-r"]

    def test_falls_back_to_calculate_theme_when_no_weekday_match(self):
        del self.themes["saturday"]
        del self.themes["sunday"]
        assert self._names(api.get_schedule(TRIGGER)) == ["off-r", "off-r"]

    def test_override_naming_a_removed_theme_is_ignored(self):
        api.set_theme_override("gone", date(2026, 1, 3), date(2026, 1, 4))
        assert api.current_theme_override() is None
        assert api.calculate_theme(date(2026, 1, 3), TRIGGER) == "saturday"
        assert self._names(api.get_schedule(TRIGGER)) == ["sat-r", "sun-r"]

    def test_override_outside_window_does_not_apply(self):
        self.themes["vacation"] = self._theme("vacation", "vac-r")
        api.set_theme_override("vacation", date(2025, 12, 1), date(2025, 12, 31))
        assert self._names(api.get_schedule(TRIGGER)) == ["sat-r", "sun-r"]


@pytest.mark.time_machine(datetime(2026, 1, 5, 7, tzinfo=config.settings.tz), tick=False)
def test_a_routine_listed_twice_in_a_day_gets_two_jobs(ctx, tmp_path):
    ctx.scheduler = Scheduler(str(tmp_path / "jobs.sqlite"), config.settings.tz)
    rule = _routine("r", time(8, 0))
    when = [datetime(2026, 1, 5, hour, tzinfo=config.settings.tz) for hour in (8, 18)]
    with patch.object(api, "get_schedule", return_value=[(at, m.ThemeEntry(at.time(), rule, "r")) for at in when]):
        api.rebuild_iot_schedule(TRIGGER)
    assert sorted(job.id for job in ctx.scheduler.jobs(m.Queue.IOT)) == ["iot-r-2026-01-05T08:00", "iot-r-2026-01-05T18:00"]


def _pause(scheduler, queue, name):
    with scheduler._lock:
        jobs = scheduler.queues()[queue]
        job = next(job for job in jobs.jobs() if job.name == name)
        jobs.remove(job.id)
        jobs.add(replace(job, disable=True))


def _rebuild_ctx(ctx, tmp_path, rules):
    ctx.scheduler = Scheduler(str(tmp_path / "jobs.sqlite"), config.settings.tz)
    entries = [
        (datetime(2026, 1, 5, 8 + i, tzinfo=config.settings.tz), m.ThemeEntry(time(8 + i, 0), _routine(n, time(8, 0)), n))
        for i, n in enumerate(rules)
    ]
    return patch.object(api, "get_schedule", return_value=entries)


@pytest.mark.time_machine(datetime(2026, 1, 5, 7, tzinfo=config.settings.tz), tick=False)
def test_two_rebuilds_keep_a_paused_job_paused(ctx, tmp_path):
    with _rebuild_ctx(ctx, tmp_path, ["a", "b"]):
        api.rebuild_iot_schedule(TRIGGER)
        _pause(ctx.scheduler, m.Queue.IOT, "a")
        api.rebuild_iot_schedule(TRIGGER)
    assert {job.id: job.disable for job in ctx.scheduler.jobs(m.Queue.IOT)} == {
        "iot-a-2026-01-05T08:00": True,
        "iot-b-2026-01-05T09:00": False,
    }


@pytest.mark.time_machine(datetime(2026, 1, 5, 7, tzinfo=config.settings.tz), tick=False)
def test_boot_sync_before_start_drops_a_removed_routines_job(ctx, tmp_path):
    with _rebuild_ctx(ctx, tmp_path, ["a", "b"]):
        runner.setup_scheduler(ctx, TRIGGER)
    with _rebuild_ctx(ctx, tmp_path, ["a"]):
        api.rebuild_iot_schedule(TRIGGER)
    assert [job.id for job in ctx.scheduler.jobs(m.Queue.IOT)] == ["iot-a-2026-01-05T08:00"]


@pytest.mark.time_machine(datetime(2026, 1, 5, 7, tzinfo=config.settings.tz), tick=False)
def test_repeated_rebuild_jobs_register_listeners_once_and_keep_a_paused_cron(ctx, tmp_path):
    with _rebuild_ctx(ctx, tmp_path, ["a"]):
        runner.setup_scheduler(ctx, TRIGGER)
        _pause(ctx.scheduler, m.Queue.ADHOC, "Iot Cron")
        for _ in range(3):
            api.rebuild_jobs(TRIGGER)
    assert {job.name: job.disable for job in ctx.scheduler.jobs(m.Queue.ADHOC)}["Iot Cron"] is True


@pytest.mark.time_machine(datetime(2026, 1, 5, 12, tzinfo=config.settings.tz), tick=False)
class TestPresence:
    ctx = object()

    @staticmethod
    def _routine(name, trigger):
        return _routine(name, time(8, 0), em.Command(m.Devices(orc.Light.a), m.OFF, tag=trigger))

    def test_mark_and_query(self):
        assert api.present_names() == set()
        api.mark_present(["Alice"], api.local_now(), TRIGGER, m.LogSource.SYSTEM)
        assert api.present_names() == {"Alice"}

    def test_expire(self):
        api.mark_present(["Alice"], api.local_now() - timedelta(minutes=1), TRIGGER, m.LogSource.SYSTEM)
        api.expire_presence(["Alice"], TRIGGER, m.LogSource.SYSTEM)
        assert api.present_names() == set()

    def test_stale_mark_expires(self):
        api.mark_present(["Alice"], datetime(2026, 1, 4, 23, 30, tzinfo=config.settings.tz), TRIGGER, m.LogSource.SYSTEM)
        assert api.present_names() == set()

    def test_run_iot_job_skips_when_presence_absent(self, command_dispatch):
        rule = self._routine("partner-r", m.Present("Alice"))
        api.run_iot_job(m.IotJob(rule, "r"))
        command_dispatch.assert_not_called()

    def test_run_iot_job_runs_when_presence_present(self, command_dispatch):
        api.mark_present(["Alice"], api.local_now(), TRIGGER, m.LogSource.SYSTEM)
        rule = self._routine("partner-r", m.Present("Alice"))
        api.run_iot_job(m.IotJob(rule, "r"))
        command_dispatch.assert_called_once_with(
            api.squish((em.Command(m.Devices(orc.Light.a), m.OFF, tag=m.Present("Alice")),)), force=False, trigger=ANY
        )

    def test_run_iot_job_runs_when_no_presence_required(self, command_dispatch):
        rule = _routine("r", time(8, 0), em.Command(m.Devices(orc.Light.a), m.OFF))
        api.run_iot_job(m.IotJob(rule, "r"))
        command_dispatch.assert_called_once_with(api.squish((em.Command(m.Devices(orc.Light.a), m.OFF),)), force=False, entry=ANY)

    def test_run_iot_job_system_trigger_bypasses_presence(self, command_dispatch):
        rule = self._routine("reset-r", em.Always())
        api.run_iot_job(m.IotJob(rule, "r"))
        command_dispatch.assert_called_once_with(
            api.squish((em.Command(m.Devices(orc.Light.a), m.OFF, tag=em.Always()),)), force=False, trigger=ANY
        )

    def test_run_iot_job_anyone_trigger_runs_when_someone_present(self, command_dispatch):
        api.mark_present(["Bob"], api.local_now(), TRIGGER, m.LogSource.SYSTEM)
        rule = self._routine("anyone-r", m.Anyone())
        api.run_iot_job(m.IotJob(rule, "r"))
        command_dispatch.assert_called_once_with(
            api.squish((em.Command(m.Devices(orc.Light.a), m.OFF, tag=m.Anyone()),)), force=False, trigger=ANY
        )

    def test_run_iot_job_anyone_trigger_skips_when_no_one_present(self, command_dispatch):
        rule = self._routine("anyone-r", m.Anyone())
        api.run_iot_job(m.IotJob(rule, "r"))
        command_dispatch.assert_not_called()

    def test_skip_log_blames_absence(self, command_dispatch):
        rule = self._routine("sunny-r", m.Weather(m.WeatherCondition.SUNNY))
        api.run_iot_job(m.IotJob(rule, "r"))
        command_dispatch.assert_not_called()
        assert any("nobody home" in c.action for c in api.context().log.rows()[0].children)

    def test_run_iot_job_skip_log_lists_weather_when_someone_home(self, command_dispatch):
        api.mark_present(["Alice"], api.local_now(), TRIGGER, m.LogSource.SYSTEM)
        rule = self._routine("cloudy-r", m.Weather(m.WeatherCondition.CLOUDY))
        api.run_iot_job(m.IotJob(rule, "r"))
        command_dispatch.assert_not_called()
        assert "CLOUDY" in api.context().log.rows()[0].action

    def test_replay_day_skips_routines_for_absent_people(self, log_entry, command_dispatch, elapsed_schedule):
        partner = self._routine("partner-r", m.Present("Alice"))
        with elapsed_schedule(partner, "partner-r"):
            api.replay_day(api.local_now(), log_entry.trigger)
        command_dispatch.assert_called_once_with((), force=True, trigger=log_entry.trigger)

    def test_replay_day_runs_routines_for_present_people(self, log_entry, command_dispatch, elapsed_schedule):
        api.mark_present(["Alice"], api.local_now(), TRIGGER, m.LogSource.SYSTEM)
        partner = self._routine("partner-r", m.Present("Alice"))
        with elapsed_schedule(partner, "partner-r"):
            api.replay_day(api.local_now(), log_entry.trigger)
        squished = command_dispatch.call_args.args[0]
        assert [(c.subject.one(), c.value) for c in squished] == [(orc.Light.a, m.OFF)]

    def test_replay_day_skips_skip_replay_routines(self, log_entry, command_dispatch, elapsed_schedule):
        api.mark_present(["Alice"], api.local_now(), TRIGGER, m.LogSource.SYSTEM)
        meeting = replace(self._routine("meeting-r", m.Present("Alice")), tags=frozenset({m.SKIP_REPLAY_TAG}))
        with elapsed_schedule(meeting, "meeting-r"):
            api.replay_day(api.local_now(), log_entry.trigger)
        command_dispatch.assert_called_once_with((), force=True, trigger=log_entry.trigger)

    def test_check_presence_continues_when_one_host_fails_to_resolve(self, fake_lan):
        def resolve(host):
            if host == "alice.local":
                raise RuntimeError("dns boom")
            return "10.0.0.2"

        with (
            patch.object(
                config,
                "people",
                (
                    m.Person("Alice", (m.Host("alice.local", "aa:aa:aa:aa:aa:aa"),)),
                    m.Person("Bob", (m.Host("bob.local", "bb:bb:bb:bb:bb:bb"),)),
                ),
            ),
            patch.object(net.socket, "gethostbyname", side_effect=resolve),
        ):
            api.check_presence(TRIGGER)
        assert api.present_names() == {"Bob"}

    def test_check_presence_with_only_tags_reads_memory(self):
        with (
            patch.object(config, "people", ()),
            patch.object(net.presence, "tagged", frozenset({"Alice"})),
        ):
            api.mark_present(["Alice"], api.local_now(), TRIGGER, m.LogSource.SYSTEM)
            assert api.check_presence(TRIGGER, m.LogSource.SYSTEM) == {"Alice"}

    def test_pause_hides_earlier_evidence_until_resume(self):
        api.mark_present(["Alice"], api.local_now() - timedelta(minutes=1), TRIGGER, m.LogSource.SYSTEM)
        api.pause_presence()
        assert api.present_names() == set()
        api.resume_presence(TRIGGER)
        assert api.present_names() == {"Alice"}

    def test_marks_during_pause_count(self):
        api.pause_presence()
        api.mark_present(["Alice"], api.local_now() + timedelta(seconds=1), TRIGGER, m.LogSource.SYSTEM)
        assert api.present_names() == {"Alice"}

    def test_future_checkin_survives_pause_and_expiry(self):
        api.mark_present(["Alice"], api.local_now() + timedelta(hours=1), TRIGGER, m.LogSource.SYSTEM)
        api.pause_presence()
        api.expire_presence(["Alice"], TRIGGER)
        assert api.present_names() == {"Alice"}

    def test_force_expire_drops_future_checkin(self):
        api.mark_present(["Alice"], api.local_now() + timedelta(hours=1), TRIGGER, m.LogSource.SYSTEM)
        api.expire_presence(["Alice"], TRIGGER, m.LogSource.SYSTEM, force=True)
        assert api.present_names() == set()

    @staticmethod
    def _reported(log_entry):
        return [log_entry.action, *(c.action for c in log_entry.children)]

    def test_mark_reports_detected_once(self):
        api.mark_present(["Alice"], api.local_now(), TRIGGER, m.LogSource.SYSTEM)
        first = api.context().log.rows()[0]
        assert any("Presence detected: `Alice`" in a for a in self._reported(first))
        before = self._reported(first)
        api.mark_present(["Alice"], api.local_now(), TRIGGER, m.LogSource.SYSTEM)
        assert api.context().log.rows()[0] is first and self._reported(first) == before

    def test_report_waits_for_resume(self):
        api.pause_presence()
        entries = len(api.context().log.rows())
        api.mark_present(["Alice"], api.local_now() + timedelta(seconds=1), TRIGGER, m.LogSource.SYSTEM)
        assert len(api.context().log.rows()) == entries
        api.resume_presence(TRIGGER)
        assert any("Presence detected: `Alice`" in a for a in self._reported(api.context().log.rows()[0]))

    def test_report_logs_lost_after_expiry(self):
        api.mark_present(["Alice"], api.local_now(), TRIGGER, m.LogSource.SYSTEM)
        api.expire_presence(["Alice"], TRIGGER, m.LogSource.SYSTEM, force=True)
        assert "Presence lost: `Alice`" in api.context().log.rows()[0].children[-1].action

    def test_check_probes_stale_tags_by_default_and_every_absent_one_when_asked(self):
        api.mark_present(["Bob"], api.local_now(), TRIGGER, m.LogSource.SYSTEM)
        api.mark_present(["Alice"], api.local_now() - timedelta(hours=10), TRIGGER, m.LogSource.SYSTEM)
        with (
            patch.object(net.presence, "tagged", frozenset({"Alice", "Bob", "Carol"})),
            patch.object(net.presence, "probe") as probe,
            patch.object(net, "scan_presence", return_value=(set(), [])),
        ):
            api.check_presence(TRIGGER)
            probe.assert_called_once_with({"Alice"})
            probe.reset_mock()
            api.check_presence(TRIGGER, probe=True)
        probe.assert_called_once_with({"Alice", "Carol"})


@pytest.mark.parametrize(
    "cfg, expected",
    [
        (
            [_cmd(Light.a, 10), _cmd(Light.a, m.ON), _cmd(Light.a, 20), _cmd(Light.a, m.ON), _cmd(Light.a, m.OFF)],
            (_cmd(Light.a, 20), _cmd(Light.a, m.OFF)),
        ),
        ([_cmd(Light.a, m.ON), _cmd(Light.a, m.OFF)], (_cmd(Light.a, m.OFF),)),
        ([_cmd(Light.a, 20), _cmd(Light.a, m.ON)], (_cmd(Light.a, 20), _cmd(Light.a, m.ON))),
        ([_cmd(Light.a, m.OFF), _cmd(Light.a, m.ON)], (_cmd(Light.a, m.ON),)),
    ],
    ids=["dim_then_off", "just_off", "dim_on", "just_on"],
)
def test_squish_one_device(cfg, expected):
    assert api._squish(cfg) == expected


@pytest.mark.parametrize(
    "commands, expected",
    [
        ((_cmd(Light, m.OFF), _cmd(Light.a, m.ON)), (_cmd(Light.a, m.ON), _cmd(Light.b, m.OFF), _cmd(Light.c, m.OFF))),
        ((_cmd(Light, m.ON), _cmd(Light.a, m.ON)), (_cmd(Light.a, m.ON), _cmd(Light.b, m.ON), _cmd(Light.c, m.ON))),
        (
            (_cmd(Light, m.OFF), _cmd(Light.a, 10), _cmd(Light, m.OFF)),
            (_cmd(Light.a, 10), _cmd(Light.a, m.OFF), _cmd(Light.b, m.OFF), _cmd(Light.c, m.OFF)),
        ),
    ],
    ids=["everything_off_start", "double_on", "dim_then_off"],
)
def test_squish_expands(commands, expected):
    assert api.squish(commands) == expected


def test_squish_stop_then_volume():
    commands = (_cmd(Chromecast, "stop"), _cmd(Chromecast, "stop"), _cmd(Chromecast.x, 10))
    assert api.squish(commands) == (_cmd(Chromecast.x, "stop"), _cmd(Chromecast.x, 10))


def test_op_cmp_dim():
    assert api._op_cmp(_cmd(Light.a, 50)) == (0, -1)


def test_op_cmp_on():
    assert api._op_cmp(_cmd(Light.a, m.ON)) == (0, 0)


def test_op_cmp_off():
    assert api._op_cmp(_cmd(Light.a, m.OFF)) == (0, 1)


def test_op_cmp_sorts_dim_before_on_before_off():
    commands = [_cmd(Light.a, m.OFF), _cmd(Light.b, m.ON), _cmd(Light.c, 50)]
    assert sorted(commands, key=api._op_cmp) == [_cmd(Light.c, 50), _cmd(Light.b, m.ON), _cmd(Light.a, m.OFF)]


def test_op_cmp_sorts_by_type_sort():
    commands = [_cmd(Chromecast.x, m.ON), _cmd(Light.a, m.ON)]
    assert sorted(commands, key=api._op_cmp) == [_cmd(Light.a, m.ON), _cmd(Chromecast.x, m.ON)]


def _routine(name, when, *commands, skip_replay=False):
    steps = tuple(em.Step(c.tag or em.And(), c) for c in commands)
    tags = frozenset({m.SKIP_REPLAY_TAG}) if skip_replay else frozenset()
    return em.Rule(steps, name=name, tags=tags)


def _ac(**attributes):
    return patch.object(broker, "snapshot", return_value=[m.DeviceState(m.Device("clip-1", "unit", "lg_ac"), attributes, None)])


def test_runtime_does_not_report_a_change_that_only_differs_by_an_unsaid_field():
    subject = em.Subject()
    values = {subject: m.AcState(m.ON, "cool", "low", 72)}
    runtime = api.runtime()
    runtime.evaluate([em.Rule((em.Step(em.Has(subject), em.Command(subject, "x")),))], read=values.__getitem__, force=True)
    values[subject] = m.AcState(m.ON, "cool")
    assert runtime.world(values.__getitem__).changed(subject) is None
    values[subject] = m.AcState(m.ON, "heat")
    assert runtime.world(values.__getitem__).changed(subject) == (m.AcState(m.ON, "cool", "low", 72), m.AcState(m.ON, "heat"))


def test_squish_logs_conflicting_arms_for_states_that_differ_by_an_unsaid_field():
    commands = (
        em.Command(m.Devices(orc.Light.a), m.LightState(switch=m.ON)),
        em.Command(m.Devices(orc.Light.a), m.LightState(switch=m.ON, level=50)),
    )
    squished = api.squish(commands, replace(TRIGGER, log_source=m.LogSource.MANUAL))
    assert [c.value for c in squished] == [m.LightState(switch=m.ON, level=50)]
    assert any(c.args[1] == api.Message.CONFLICTING_ARMS.format(device=orc.Light.a.name, states="on, 50") for c in entry.add.call_args_list)
