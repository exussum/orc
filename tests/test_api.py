from dataclasses import replace
from datetime import date, datetime, time, timedelta
from unittest.mock import ANY, MagicMock, call, create_autospec, patch

import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from freezegun import freeze_time
from orc_engine import model as em

import orc
from orc import api, config, plugins
from orc import model as m
from orc.dal import broker, net, push, scheduler, sqlite
from orc.dal.findhub import stub as findhub_stub
from orc.model import DeviceEnum

FUTURE = datetime(2100, 1, 1, tzinfo=config.settings.tz)
TRIGGER = m.Query("test")
PAST = datetime(2000, 1, 1, tzinfo=config.settings.tz)


class Light(DeviceEnum):
    a = 1
    b = 2
    c = 3


class Chromecast(DeviceEnum):
    x = 1


Light._sort = 0
Chromecast._sort = 1


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
    api._ctx.engine.save_snapshot(api.ORC_SYSTEM_SNAPSHOT, m.SnapShot(routine=snapshot_commands, end=FUTURE), FUTURE)


@pytest.fixture
def log_entry():
    return m.LogEntry(FUTURE, m.LogSource.MANUAL, "test", TRIGGER)


@pytest.fixture
def push_subscriptions():
    def build(names):
        return [m.PushSubscription(f"https://push.example/{name}", "public-key", "auth-secret") for name in names]

    return build


class TestRestoreScene:
    def test_restore_with_snapshot(self, command_dispatch, snapshot_commands, log_entry):
        api._ctx.engine.save_snapshot("test", m.SnapShot(routine=snapshot_commands, end=FUTURE), FUTURE)
        api.restore_scene("test", (), log_entry)
        assert command_dispatch.call_args_list == [call(snapshot_commands, force=True, entry=log_entry)]

    def test_restore_without_snapshot(self, command_dispatch, snapshot_commands, log_entry):
        api.restore_scene("test", snapshot_commands, log_entry)
        assert command_dispatch.call_args_list == [call(snapshot_commands, force=True, entry=log_entry)]

    def test_restore_drops_expired_snapshot(self, command_dispatch, snapshot_commands, log_entry):
        api._ctx.engine.save_snapshot("test", m.SnapShot(routine=snapshot_commands, end=PAST), PAST)
        api.restore_scene("test", snapshot_commands, log_entry)
        assert command_dispatch.call_args_list == [call(snapshot_commands, force=True, entry=log_entry)]
        assert not api._ctx.engine.snapshots()

    def test_pop_snapshot_once_and_never_expired(self, command_dispatch, snapshot_commands):
        api._ctx.engine.save_snapshot("test", m.SnapShot(routine=snapshot_commands, end=FUTURE), FUTURE)
        assert api._ctx.engine.pop_snapshot("test").routine is snapshot_commands
        assert api._ctx.engine.pop_snapshot("test") is None
        api._ctx.engine.save_snapshot("test", m.SnapShot(routine=snapshot_commands, end=PAST), PAST)
        assert api._ctx.engine.pop_snapshot("test") is None
        command_dispatch.assert_not_called()


@patch("orc.dal.broker.stub.command")
class TestIntercepts:
    def test_system_command_overwrites_snapshot(self, update_light, system_snapshot, log_entry):
        command = em.Command(m.Devices(orc.Light.b), m.ON, tag=m.Always())

        api.dispatch((command,), entry=log_entry)
        api.dispatch((command,), entry=log_entry)

        assert api._ctx.engine.snapshots()[api.ORC_SYSTEM_SNAPSHOT].routine == (
            em.Command(m.Devices(orc.Light.a), m.ON),
            em.Command(m.Devices(orc.Light.b), m.ON, tag=m.Always()),
        )
        assert update_light.call_args_list == [call(orc.Light.b, m.ON), call(orc.Light.b, m.ON)]

    def test_snapshot_update_add(self, update_light, system_snapshot, log_entry):
        command = em.Command(m.Devices(orc.Light.c), m.ON, tag=m.Always())

        api.dispatch((command,), entry=log_entry)

        assert api._ctx.engine.snapshots()[api.ORC_SYSTEM_SNAPSHOT].routine == (
            em.Command(m.Devices(orc.Light.a), m.ON),
            em.Command(m.Devices(orc.Light.b), m.OFF),
            command,
        )
        assert update_light.call_args_list == [call(orc.Light.c, m.ON)]

    def test_untagged_command_suppressed(self, update_light, system_snapshot, log_entry):
        command = em.Command(m.Devices(orc.Light.c), m.ON)

        api.dispatch((command,), entry=log_entry)

        assert api._ctx.engine.snapshots()[api.ORC_SYSTEM_SNAPSHOT].routine == (
            em.Command(m.Devices(orc.Light.a), m.ON),
            em.Command(m.Devices(orc.Light.b), m.OFF),
        )
        assert update_light.call_args_list == []

    def test_expired_snapshot_drops(self, update_light, snapshot_commands, log_entry):
        command = em.Command(m.Devices(orc.Light.c), m.ON)

        api._ctx.engine.save_snapshot(api.ORC_SYSTEM_SNAPSHOT, m.SnapShot(routine=snapshot_commands, end=PAST), PAST)
        api.dispatch((command,), entry=log_entry)

        assert not api._ctx.engine.snapshots()
        assert update_light.call_args_list == [call(orc.Light.c, m.ON)]

    def test_unrelated_plugin_snapshot_does_not_suppress(self, update_light, snapshot_commands, log_entry):
        command = em.Command(m.Devices(orc.Light.c), m.ON)

        api._ctx.engine.save_snapshot("entrance_sensor", m.SnapShot(routine=snapshot_commands, end=FUTURE), FUTURE)
        api.dispatch((command,), entry=log_entry)

        assert update_light.call_args_list == [call(orc.Light.c, m.ON)]

    def test_snapshot_bypassed(self, update_light, system_snapshot, log_entry):
        command = em.Command(m.Devices(orc.Light.c), m.ON)

        api.dispatch((command,), force=True, entry=log_entry)

        assert api._ctx.engine.snapshots()[api.ORC_SYSTEM_SNAPSHOT].routine == (
            em.Command(m.Devices(orc.Light.a), m.ON),
            em.Command(m.Devices(orc.Light.b), m.OFF),
        )
        assert update_light.call_args_list == [call(orc.Light.c, m.ON)]

    def test_force_off_is_not_recorded_and_resume_relights(self, update_light, system_snapshot, log_entry):
        api.dispatch((em.Command(m.Devices(orc.Light.a), m.OFF),), force=True, entry=log_entry)  # room control during the scene

        assert api._ctx.engine.snapshots()[api.ORC_SYSTEM_SNAPSHOT].routine == (
            em.Command(m.Devices(orc.Light.a), m.ON),
            em.Command(m.Devices(orc.Light.b), m.OFF),
        )

        api.restore_scene(api.ORC_SYSTEM_SNAPSHOT, (), log_entry)

        assert update_light.call_args_list == [
            call(orc.Light.a, m.OFF),  # the deliberate off
            call(orc.Light.a, m.ON),  # resume undoes it
            call(orc.Light.b, m.OFF),
        ]


def test_dispatch_usb_sets_volume(log_entry):
    from orc.dal.audio import stub as audio_stub

    api.dispatch((em.Command(m.Devices(orc.USB.speaker), 50),), force=True, entry=log_entry)

    assert audio_stub._volumes == {orc.USB.speaker: 50}


def test_dispatch_usb_plays_alert_path(log_entry):
    from orc.dal.audio import stub as audio_stub

    api.dispatch((em.Command(m.Devices(orc.USB.speaker), "/tmp/alert.wav"),), force=True, entry=log_entry)

    assert audio_stub._alerted == ["/tmp/alert.wav"]


def test_back_on_schedule_checks_presence_then_replays(log_entry):
    ctx = MagicMock()
    ctx.api = create_autospec(api)
    plugins.back_on_schedule(ctx, None, entry=log_entry)
    ctx.api.check_presence.assert_called_once_with(log_entry.trigger)
    ctx.api.replay_day.assert_called_once_with(ctx.api.local_now.return_value, log_entry)


def test_button_ad_hoc_snapshot(ctx, config_ad_hoc, command_dispatch):
    action = m.AdhocAction((em.Command(m.Devices(orc.Light.b), m.ON),), snapshot=timedelta(hours=3))
    captured = (em.Command(m.Devices(orc.Light.a), m.ON),)
    with config_ad_hoc({"r": action}), patch.object(api, "capture_lights", return_value=captured):
        api.run_action(ctx, "r", m.Button("1"), source=m.LogSource.EXTERNAL)
    snap = ctx.engine.snapshots()[api.ORC_SYSTEM_SNAPSHOT]
    assert snap.routine is captured
    assert snap.end > api.local_now()
    command_dispatch.assert_called_once_with(action.commands, force=True, entry=ANY)


def test_button_ad_hoc_snapshot_does_not_stack(ctx, config_ad_hoc, command_dispatch, light_capture):
    action = m.AdhocAction((em.Command(m.Devices(orc.Light.b), m.ON),), snapshot=timedelta(hours=3))
    reset = _routine("reset", "", em.Command(m.Devices(orc.Light.a), m.OFF))
    existing = (em.Command(m.Devices(orc.Light.a), m.ON),)
    snap = m.SnapShot(routine=existing, end=api.local_now() + timedelta(hours=1))
    ctx.engine.save_snapshot(api.ORC_SYSTEM_SNAPSHOT, snap, snap.end)
    with config_ad_hoc({"r": action}, reset_config=reset):
        api.run_action(ctx, "r", m.Button("1"), source=m.LogSource.EXTERNAL)
    # Existing snapshot is preserved (not popped, not overwritten) and no new one is taken.
    assert ctx.engine.snapshots()[api.ORC_SYSTEM_SNAPSHOT].routine is existing
    light_capture.assert_not_called()
    command_dispatch.assert_called_once_with((*reset.commands, *action.commands), force=True, entry=ANY)


def test_dispatch_routes_ac_commands_to_the_broker(log_entry):
    with patch.object(config.providers.broker, "command") as command:
        api.dispatch(
            (em.Command(m.Devices(orc.AC.unit), m.AcState(m.ON, m.AcMode.COOL, "low", temperature=75)),), force=True, entry=log_entry
        )
        api.dispatch((em.Command(m.Devices(orc.AC.unit), m.AcState(power=m.ON)),), force=True, entry=log_entry)
        api.dispatch((em.Command(m.Devices(orc.AC.unit), m.AcState(power=m.OFF)),), force=True, entry=log_entry)
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
        api.dispatch((em.Command(m.Devices(orc.Light.a), m.ON),), force=True, entry=log_entry)
    assert log_entry.children[-1].action == "Dispatch failed for `a`: boom"


def test_dispatch_usb_speaks_state_error(log_entry):
    from orc.dal.audio import stub as audio_stub

    api.dispatch((em.Command(m.Devices(orc.USB.speaker), m.ON),), force=True, entry=log_entry)

    assert len(audio_stub._spoken) == 1
    assert "USB devices don't support state" in audio_stub._spoken[0]


class TestRefreshTagKeys:
    @pytest.fixture(autouse=True)
    def reset_log(self):
        api._ACTIVITY_LOG.clear()

    def test_a_new_key_reaches_the_row_and_the_listener(self):
        with patch.object(net.presence, "set_key") as set_key:
            api.refresh_tag_keys(m.Manual("secrets"))
        key = m.BleKey(b"Alice Tag".ljust(32, b"\0"), 0)
        assert sqlite.fetch_tags() == [m.Tag("Alice", key, None)]
        set_key.assert_called_once_with("Alice", key)
        assert [e.action for e in api.log_entries()] == ["Tag key for `Alice` read from Find Hub: e595fa93, paired 1969-12-31 19:00"]

    def test_an_unchanged_key_is_silent(self):
        api.refresh_tag_keys(m.Manual("secrets"))
        api._ACTIVITY_LOG.clear()
        api.refresh_tag_keys(m.Manual("secrets"))
        assert api.log_entries() == []

    def test_a_failed_lookup_logs_and_keeps_going(self, monkeypatch):
        monkeypatch.setattr(findhub_stub, "identity_key", MagicMock(side_effect=RuntimeError("boom")))
        api.refresh_tag_keys(m.Manual("secrets"))
        assert sqlite.fetch_tags() == []
        assert [e.action for e in api.log_entries()] == ["Find Hub lookup failed for `Alice`: boom"]


class TestLog:
    @pytest.fixture(autouse=True)
    def reset_log(self):
        api._ACTIVITY_LOG.clear()

    def test_a_burst_from_one_trigger_nests_under_the_entry_it_opened(self):
        api.log(m.LogSource.PLUGIN, "first", m.Integration("x"))
        api.log(m.LogSource.PLUGIN, "second", m.Integration("x"))
        entries = api.log_entries()
        assert [e.action for e in entries] == ["first"]
        assert [c.action for c in entries[0].children] == ["second"]

    def test_a_swarm_of_external_changes_rolls_up(self):
        with freeze_time(datetime(2026, 1, 5, 12, tzinfo=config.settings.tz)) as frozen:
            api._log_external(m.Status(m.Device("1", "lamp a", "hubitat"), "switch", "off", "on", m.Source.EXTERNAL))
            api._log_external(m.Status(m.Device("2", "lamp b", "hubitat"), "switch", "off", "on", m.Source.EXTERNAL))
            api._log_external(m.Status(m.Device("2", "lamp b", "hubitat"), "switch", "on", "off", m.Source.ORC))
            frozen.tick(api._ROLLUP_WINDOW)
            api._log_external(m.Status(m.Device("1", "lamp a", "hubitat"), "switch", "on", "off", m.Source.EXTERNAL))
        assert [(e.action, [c.action for c in e.children]) for e in api.log_entries()] == [
            ("`lamp a` switch: on → off", []),
            ("`lamp a` switch: off → on", ["`lamp b` switch: off → on"]),
        ]

    def test_a_different_trigger_starts_its_own_entry(self):
        api.log(m.LogSource.PLUGIN, "first", m.Integration("x"))
        api.log(m.LogSource.PLUGIN, "other", m.Integration("y"))
        entries = api.log_entries()
        assert [e.action for e in entries] == ["other", "first"]
        assert entries[0].children == []

    def test_sources_differ_but_the_trigger_is_one_so_they_nest(self):
        api.log(m.LogSource.SYSTEM, "sys", m.Integration("x"))
        api.log(m.LogSource.PLUGIN, "plug", m.Integration("x"))
        entries = api.log_entries()
        assert [e.action for e in entries] == ["sys"]
        assert [c.action for c in entries[0].children] == ["plug"]

    def test_the_trigger_an_entry_started_with_nests_however_late(self):
        with freeze_time(datetime(2026, 1, 5, 12, tzinfo=config.settings.tz)) as frozen:
            entry = api.log(m.LogSource.PLUGIN, "first", m.Integration("x"))
            frozen.tick(timedelta(hours=1))
            api.log(m.LogSource.PLUGIN, "later", entry.trigger)
        assert [(e.action, [c.action for c in e.children]) for e in api.log_entries()] == [("first", ["later"])]

    def test_an_equal_trigger_after_the_window_starts_its_own_entry(self):
        with freeze_time(datetime(2026, 1, 5, 12, tzinfo=config.settings.tz)) as frozen:
            api.log(m.LogSource.PLUGIN, "first", m.Integration("x"))
            frozen.tick(api._ROLLUP_WINDOW)
            api.log(m.LogSource.PLUGIN, "later", m.Integration("x"))
        assert [(e.action, [c.action for c in e.children]) for e in api.log_entries()] == [("later", []), ("first", [])]

    def test_a_nested_line_still_notifies(self):
        api.log(m.LogSource.PLUGIN, "first", m.Integration("x"))
        with freeze_time(datetime(2026, 1, 5, 12, tzinfo=config.settings.tz)):
            api.log(m.LogSource.PLUGIN, "later `x`", m.Integration("x"), notification=m.Notification(("calendar", "dentist")))
        assert api._ctx.scheduler.now.call_args.args[1:] == (
            "[Plugin 01/05]",
            "later x",
            m.Notification(("calendar", "dentist")),
            m.Integration("x"),
            None,
        )

    def test_a_link_rides_the_push_as_given(self):
        notification = m.Notification(("calendar",), "https://meet.google.com/abc?x=1")
        with freeze_time(datetime(2026, 1, 5, 12, tzinfo=config.settings.tz)):
            api.log(m.LogSource.PLUGIN, "standup", m.Integration("x"), notification=notification)
        assert api._ctx.scheduler.now.call_args.args[3] == notification

    def test_a_greeted_subscription_is_pushed_alone(self, push_provider, push_subscriptions):
        push_subscriptions = push_subscriptions("ab")
        api.subscribe_push(push_subscriptions[0])
        with freeze_time(datetime(2026, 1, 5, 12, tzinfo=config.settings.tz)):
            api.subscribe_push(push_subscriptions[1], greet=True)
        now = api._ctx.scheduler.now
        assert now.call_args.args[0] is api._push_job
        assert now.call_args.args[1:] == (
            "[System 01/05]",
            "Notifications enabled on this device",
            m.Notification(("greeting",)),
            m.Manual("notify"),
            (push_subscriptions[1],),
        )
        assert set(sqlite.fetch_push_subscriptions()) == set(push_subscriptions)

    def test_unsubscribing_drops_only_that_endpoint(self, push_subscriptions):
        push_subscriptions = push_subscriptions("ab")
        for subscription in push_subscriptions:
            api.subscribe_push(subscription)
        api.unsubscribe_push(push_subscriptions[0].endpoint)
        assert sqlite.fetch_push_subscriptions() == [push_subscriptions[1]]

    def test_a_push_reaches_every_subscription_and_drops_gone_ones(self, push_provider, push_subscriptions):
        push_subscriptions = push_subscriptions("abc")
        for subscription in push_subscriptions:
            api.subscribe_push(subscription)
        push_provider.send.side_effect = [None, push.Gone("b"), RuntimeError("boom")]
        entry = api.log(m.LogSource.PLUGIN, "Leak at `kitchen`", m.Integration("x"), notification=m.Notification(("leak",)))
        api._push_job("[Plugin 01/05]", "Leak at kitchen", "leak", entry.trigger, None, ctx=api._ctx)
        assert push_provider.send.call_args_list == [call(s, "[Plugin 01/05]", "Leak at kitchen", "leak") for s in push_subscriptions]
        assert set(sqlite.fetch_push_subscriptions()) == {push_subscriptions[0], push_subscriptions[2]}
        assert [c.action for c in entry.children] == ["Push failed for `…xample/c`: boom"]


@freeze_time(datetime(2026, 1, 5, 12, tzinfo=config.settings.tz))
class TestActiveOverride:
    OVERRIDE = m.ThemeOverride("vacation", date(2026, 1, 1), date(2026, 1, 10))

    @pytest.fixture(autouse=True)
    def theme_override(self):
        api.set_theme_override(*self.OVERRIDE)

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


@freeze_time(datetime(2026, 1, 5, 12, tzinfo=config.settings.tz))
class TestIsWorkingDay:
    def test_a_weekday_that_the_market_trades_is_a_working_day(self):
        assert api.is_working_day(date(2026, 1, 5)) is True

    def test_a_weekend_is_not(self):
        assert api.is_working_day(date(2026, 1, 3)) is False

    def test_a_market_holiday_is_not(self):
        with patch.object(config.providers.holiday, "market_holiday", return_value=True):
            assert api.is_working_day(date(2026, 1, 5)) is False

    def test_an_override_decides_it(self):
        api.set_theme_override("day off", date(2026, 1, 5), date(2026, 1, 5))
        assert api.is_working_day(date(2026, 1, 5)) is False


# 2026-01-03 is Saturday, 2026-01-04 is Sunday
@freeze_time(datetime(2026, 1, 3, 12, tzinfo=config.settings.tz))
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
        assert self._names(api.get_schedule()) == ["vac-r", "vac-r"]

    def test_empty_override_clears_weekday_named_theme(self):
        self.themes["empty"] = self._theme("empty")
        api.set_theme_override("empty", date(2026, 1, 3), date(2026, 1, 4))
        assert self._names(api.get_schedule()) == []

    def test_weekday_named_theme_used_when_no_override(self):
        assert self._names(api.get_schedule()) == ["sat-r", "sun-r"]

    def test_falls_back_to_calculate_theme_when_no_weekday_match(self):
        del self.themes["saturday"]
        del self.themes["sunday"]
        assert self._names(api.get_schedule()) == ["off-r", "off-r"]

    def test_override_outside_window_does_not_apply(self):
        self.themes["vacation"] = self._theme("vacation", "vac-r")
        api.set_theme_override("vacation", date(2025, 12, 1), date(2025, 12, 31))
        assert self._names(api.get_schedule()) == ["sat-r", "sun-r"]


@freeze_time(datetime(2026, 1, 5, 12, tzinfo=config.settings.tz))
class TestPresence:
    ctx = object()  # run_iot_job never reads it; requires_ctx only rejects None

    @staticmethod
    def _routine(name, trigger):
        return _routine(name, time(8, 0), em.Command(m.Devices(orc.Light.a), m.OFF, tag=trigger))

    def test_mark_and_query(self):
        assert api.present_names() == set()
        api.mark_present(["Alice"], api.local_now(), TRIGGER)
        assert api.present_names() == {"Alice"}

    def test_expire(self):
        api.mark_present(["Alice"], api.local_now() - timedelta(minutes=1), TRIGGER)
        api.expire_presence(["Alice"], TRIGGER)
        assert api.present_names() == set()

    def test_stale_mark_expires(self):
        api.mark_present(["Alice"], datetime(2026, 1, 4, 23, 30, tzinfo=config.settings.tz), TRIGGER)
        assert api.present_names() == set()

    def test_run_iot_job_skips_when_presence_absent(self, command_dispatch):
        rule = self._routine("partner-r", m.Present("Alice"))
        api.run_iot_job(m.IotJob(rule, "r"), ctx=self.ctx)
        command_dispatch.assert_not_called()

    def test_run_iot_job_runs_when_presence_present(self, command_dispatch):
        api.mark_present(["Alice"], api.local_now(), TRIGGER)
        rule = self._routine("partner-r", m.Present("Alice"))
        api.run_iot_job(m.IotJob(rule, "r"), ctx=self.ctx)
        command_dispatch.assert_called_once_with(
            api.squish((em.Command(m.Devices(orc.Light.a), m.OFF, tag=m.Present("Alice")),)), force=False, entry=ANY
        )

    def test_run_iot_job_runs_when_no_presence_required(self, command_dispatch):
        rule = _routine("r", time(8, 0), em.Command(m.Devices(orc.Light.a), m.OFF))
        api.run_iot_job(m.IotJob(rule, "r"), ctx=self.ctx)
        command_dispatch.assert_called_once_with(api.squish((em.Command(m.Devices(orc.Light.a), m.OFF),)), force=False, entry=ANY)

    def test_run_iot_job_system_trigger_bypasses_presence(self, command_dispatch):
        rule = self._routine("reset-r", m.Always())
        api.run_iot_job(m.IotJob(rule, "r"), ctx=self.ctx)
        command_dispatch.assert_called_once_with(
            api.squish((em.Command(m.Devices(orc.Light.a), m.OFF, tag=m.Always()),)), force=False, entry=ANY
        )

    def test_run_iot_job_anyone_trigger_runs_when_someone_present(self, command_dispatch):
        api.mark_present(["Bob"], api.local_now(), TRIGGER)
        rule = self._routine("anyone-r", m.Anyone())
        api.run_iot_job(m.IotJob(rule, "r"), ctx=self.ctx)
        command_dispatch.assert_called_once_with(
            api.squish((em.Command(m.Devices(orc.Light.a), m.OFF, tag=m.Anyone()),)), force=False, entry=ANY
        )

    def test_run_iot_job_anyone_trigger_skips_when_no_one_present(self, command_dispatch):
        rule = self._routine("anyone-r", m.Anyone())
        api.run_iot_job(m.IotJob(rule, "r"), ctx=self.ctx)
        command_dispatch.assert_not_called()

    def test_skip_log_blames_absence(self, command_dispatch):
        rule = self._routine("sunny-r", m.Weather(m.WeatherCondition.SUNNY))
        api.run_iot_job(m.IotJob(rule, "r"), ctx=self.ctx)
        command_dispatch.assert_not_called()
        assert "nobody home" in api.log_entries()[0].action

    def test_run_iot_job_skip_log_lists_weather_when_someone_home(self, command_dispatch):
        api.mark_present(["Alice"], api.local_now(), TRIGGER)
        rule = self._routine("cloudy-r", m.Weather(m.WeatherCondition.CLOUDY))
        api.run_iot_job(m.IotJob(rule, "r"), ctx=self.ctx)
        command_dispatch.assert_not_called()
        assert "CLOUDY" in api.log_entries()[0].action

    def test_replay_day_skips_routines_for_absent_people(self, log_entry, command_dispatch, elapsed_schedule):
        partner = self._routine("partner-r", m.Present("Alice"))
        with elapsed_schedule(partner, "partner-r"):
            api.replay_day(api.local_now(), log_entry)
        command_dispatch.assert_called_once_with((), force=True, entry=log_entry)

    def test_replay_day_runs_routines_for_present_people(self, log_entry, command_dispatch, elapsed_schedule):
        api.mark_present(["Alice"], api.local_now(), TRIGGER)
        partner = self._routine("partner-r", m.Present("Alice"))
        with elapsed_schedule(partner, "partner-r"):
            api.replay_day(api.local_now(), log_entry)
        squished = command_dispatch.call_args.args[0]
        assert [(c.subject.one(), c.value) for c in squished] == [(orc.Light.a, m.OFF)]

    def test_replay_day_skips_skip_replay_routines(self, log_entry, command_dispatch, elapsed_schedule):
        api.mark_present(["Alice"], api.local_now(), TRIGGER)
        meeting = replace(self._routine("meeting-r", m.Present("Alice")), tags=frozenset({m.SKIP_REPLAY_TAG}))
        with elapsed_schedule(meeting, "meeting-r"):
            api.replay_day(api.local_now(), log_entry)
        command_dispatch.assert_called_once_with((), force=True, entry=log_entry)

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
            api.mark_present(["Alice"], api.local_now(), TRIGGER)
            assert api.check_presence(TRIGGER) == {"Alice"}

    def test_pause_hides_earlier_evidence_until_resume(self):
        api.mark_present(["Alice"], api.local_now() - timedelta(minutes=1), TRIGGER)
        api.pause_presence()
        assert api.present_names() == set()
        api.resume_presence(TRIGGER)
        assert api.present_names() == {"Alice"}

    def test_marks_during_pause_count(self):
        api.pause_presence()
        api.mark_present(["Alice"], api.local_now() + timedelta(seconds=1), TRIGGER)
        assert api.present_names() == {"Alice"}

    def test_future_checkin_survives_pause_and_expiry(self):
        api.mark_present(["Alice"], api.local_now() + timedelta(hours=1), TRIGGER)
        api.pause_presence()
        api.expire_presence(["Alice"], TRIGGER)
        assert api.present_names() == {"Alice"}

    def test_force_expire_drops_future_checkin(self):
        api.mark_present(["Alice"], api.local_now() + timedelta(hours=1), TRIGGER)
        api.expire_presence(["Alice"], TRIGGER, force=True)
        assert api.present_names() == set()

    @staticmethod
    def _reported(log_entry):
        return [log_entry.action, *(c.action for c in log_entry.children)]

    def test_mark_reports_detected_once(self):
        api.mark_present(["Alice"], api.local_now(), TRIGGER)
        first = api.log_entries()[0]
        assert any("Presence detected: `Alice`" in a for a in self._reported(first))
        before = self._reported(first)
        api.mark_present(["Alice"], api.local_now(), TRIGGER)
        assert api.log_entries()[0] is first and self._reported(first) == before

    def test_report_waits_for_resume(self):
        api.pause_presence()
        entries = len(api.log_entries())
        api.mark_present(["Alice"], api.local_now() + timedelta(seconds=1), TRIGGER)
        assert len(api.log_entries()) == entries
        api.resume_presence(TRIGGER)
        assert any("Presence detected: `Alice`" in a for a in self._reported(api.log_entries()[0]))

    def test_report_logs_lost_after_expiry(self):
        api.mark_present(["Alice"], api.local_now(), TRIGGER)
        api.expire_presence(["Alice"], TRIGGER, force=True)
        assert "Presence lost: `Alice`" in api.log_entries()[0].children[-1].action

    def test_check_probes_stale_tags_by_default_and_every_absent_one_when_asked(self):
        api.mark_present(["Bob"], api.local_now(), TRIGGER)
        api.mark_present(["Alice"], api.local_now() - timedelta(hours=10), TRIGGER)
        with (
            patch.object(net.presence, "tagged", frozenset({"Alice", "Bob", "Carol"})),
            patch.object(net.presence, "probe") as probe,
            patch.object(net, "scan_presence", return_value=(set(), [])),
        ):
            api.check_presence(TRIGGER)
            probe.assert_called_once_with({"Alice"}, TRIGGER)
            probe.reset_mock()
            api.check_presence(TRIGGER, probe=True)
        probe.assert_called_once_with({"Alice", "Carol"}, TRIGGER)


def test_executor_injects_ctx():
    """_do_submit_job must not raise for closure callables (Job uses __slots__, not __dict__)."""
    ctx = object()
    executor = scheduler.ContextThreadPoolExecutor(ctx)

    def make_closure():
        def run():
            pass

        return run

    sched = BackgroundScheduler()
    sched.start()
    job = sched.add_job(make_closure(), DateTrigger(FUTURE, timezone=config.settings.tz))
    sched.shutdown(wait=False)

    captured = []
    with patch.object(scheduler.ThreadPoolExecutor, "_do_submit_job", lambda s, j, rt: captured.append(j)):
        executor._do_submit_job(job, [])

    assert captured[0].kwargs["ctx"] is ctx


def test_squish_dim_then_off():
    cfg = (_cmd(Light.a, 10), _cmd(Light.a, m.ON), _cmd(Light.a, 20), _cmd(Light.a, m.ON), _cmd(Light.a, m.OFF))
    assert api._squish(list(cfg)) == (_cmd(Light.a, 20), _cmd(Light.a, m.OFF))


def test_squish_just_off():
    cfg = [_cmd(Light.a, m.ON), _cmd(Light.a, m.OFF)]
    assert api._squish(cfg) == (_cmd(Light.a, m.OFF),)


def test_squish_dim_on():
    cfg = [_cmd(Light.a, 20), _cmd(Light.a, m.ON)]
    assert api._squish(cfg) == (_cmd(Light.a, 20), _cmd(Light.a, m.ON))


def test_squish_just_on():
    cfg = [_cmd(Light.a, m.OFF), _cmd(Light.a, m.ON)]
    assert api._squish(cfg) == (_cmd(Light.a, m.ON),)


def test_squish_expands_everything_off_start():
    commands = (_cmd(Light, m.OFF), _cmd(Light.a, m.ON))
    assert api.squish(commands) == (_cmd(Light.a, m.ON), _cmd(Light.b, m.OFF), _cmd(Light.c, m.OFF))


def test_squish_expands_double_on():
    commands = (_cmd(Light, m.ON), _cmd(Light.a, m.ON))
    assert api.squish(commands) == (_cmd(Light.a, m.ON), _cmd(Light.b, m.ON), _cmd(Light.c, m.ON))


def test_squish_expands_dim_then_off():
    commands = (_cmd(Light, m.OFF), _cmd(Light.a, 10), _cmd(Light, m.OFF))
    assert api.squish(commands) == (_cmd(Light.a, 10), _cmd(Light.a, m.OFF), _cmd(Light.b, m.OFF), _cmd(Light.c, m.OFF))


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


def _cmd(what, value):
    return em.Command(m.Devices(what), value)
