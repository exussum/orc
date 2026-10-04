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
from orc.dal import net, push, scheduler, sqlite
from orc.dal.mqtt import stub as mqtt_stub
from orc.kernel import loader

FUTURE = datetime(2100, 1, 1, tzinfo=config.settings.tz)
TRIGGER = m.Query("test")
PAST = datetime(2000, 1, 1, tzinfo=config.settings.tz)


def _routine(name, when, *commands, skip_replay=False):
    steps = tuple(em.Step(loader._condition(c.tag), c) for c in commands)
    tags = frozenset({m.SKIP_REPLAY_TAG}) if skip_replay else frozenset()
    return em.Rule(steps, name=name, tags=tags)


@pytest.fixture
def snapshot_config():
    return (em.Command(m.Devices(orc.Light.a), m.ON), em.Command(m.Devices(orc.Light.b), m.OFF))


@pytest.fixture
def entry():
    return m.LogEntry(FUTURE, m.LogSource.MANUAL, "test", TRIGGER)


@patch("orc.api.dispatch")
class TestManagingConfig:
    def test_resume_with_snapshot(self, dispatch, snapshot_config, entry):
        api._ctx.engine.save_snapshot("test", m.SnapShot(routine=snapshot_config, end=FUTURE), FUTURE)
        api.restore_scene("test", (), entry)
        assert dispatch.call_args_list == [call(snapshot_config, force=True, entry=entry)]

    def test_resume_without_snapshot(self, dispatch, snapshot_config, entry):
        api.restore_scene("test", snapshot_config, entry)
        assert dispatch.call_args_list == [call(snapshot_config, force=True, entry=entry)]

    def test_resume_with_old_snapshot(self, dispatch, snapshot_config, entry):
        api._ctx.engine.save_snapshot("test", m.SnapShot(routine=snapshot_config, end=PAST), PAST)
        api.restore_scene("test", snapshot_config, entry)
        assert dispatch.call_args_list == [call(snapshot_config, force=True, entry=entry)]
        assert not api._ctx.engine.snapshots()

    def test_get_pops_the_snapshot_once(self, dispatch, snapshot_config):
        api._ctx.engine.save_snapshot("test", m.SnapShot(routine=snapshot_config, end=FUTURE), FUTURE)
        assert api._ctx.engine.pop_snapshot("test").routine is snapshot_config
        assert api._ctx.engine.pop_snapshot("test") is None
        api._ctx.engine.save_snapshot("test", m.SnapShot(routine=snapshot_config, end=PAST), PAST)
        assert api._ctx.engine.pop_snapshot("test") is None
        dispatch.assert_not_called()


@patch("orc.dal.mqtt.stub.publish_light")
class TestIntercepts:
    def test_snapshot_update_overwrite_set(self, update_light, snapshot_config, entry):
        command = em.Command(m.Devices(orc.Light.b), m.ON, tag=m.Tag.SYSTEM)

        api._ctx.engine.save_snapshot(api.ORC_SYSTEM_SNAPSHOT, m.SnapShot(routine=snapshot_config, end=FUTURE), FUTURE)
        api.dispatch((command,), entry=entry)
        api.dispatch((command,), entry=entry)

        assert api._ctx.engine.snapshots()[api.ORC_SYSTEM_SNAPSHOT].routine == (
            em.Command(m.Devices(orc.Light.a), m.ON),
            em.Command(m.Devices(orc.Light.b), m.ON, tag=m.Tag.SYSTEM),
        )
        assert update_light.call_args_list == [call(orc.Light.b, on=True), call(orc.Light.b, on=True)]

    def test_snapshot_update_add(self, update_light, snapshot_config, entry):
        command = em.Command(m.Devices(orc.Light.c), m.ON, tag=m.Tag.SYSTEM)

        api._ctx.engine.save_snapshot(api.ORC_SYSTEM_SNAPSHOT, m.SnapShot(routine=snapshot_config, end=FUTURE), FUTURE)
        api.dispatch((command,), entry=entry)

        assert api._ctx.engine.snapshots()[api.ORC_SYSTEM_SNAPSHOT].routine == (
            em.Command(m.Devices(orc.Light.a), m.ON),
            em.Command(m.Devices(orc.Light.b), m.OFF),
            command,
        )
        assert update_light.call_args_list == [call(orc.Light.c, on=True)]

    def test_rule_ignored(self, update_light, snapshot_config, entry):
        command = em.Command(m.Devices(orc.Light.c), m.ON)

        api._ctx.engine.save_snapshot(api.ORC_SYSTEM_SNAPSHOT, m.SnapShot(routine=snapshot_config, end=FUTURE), FUTURE)
        api.dispatch((command,), entry=entry)

        assert api._ctx.engine.snapshots()[api.ORC_SYSTEM_SNAPSHOT].routine == (
            em.Command(m.Devices(orc.Light.a), m.ON),
            em.Command(m.Devices(orc.Light.b), m.OFF),
        )
        assert update_light.call_args_list == []

    def test_rule_old_snapshot(self, update_light, snapshot_config, entry):
        command = em.Command(m.Devices(orc.Light.c), m.ON)

        api._ctx.engine.save_snapshot(api.ORC_SYSTEM_SNAPSHOT, m.SnapShot(routine=snapshot_config, end=PAST), PAST)
        api.dispatch((command,), entry=entry)

        assert not api._ctx.engine.snapshots()
        assert update_light.call_args_list == [call(orc.Light.c, on=True)]

    def test_unrelated_plugin_snapshot_does_not_suppress(self, update_light, snapshot_config, entry):
        command = em.Command(m.Devices(orc.Light.c), m.ON)

        api._ctx.engine.save_snapshot("entrance_sensor", m.SnapShot(routine=snapshot_config, end=FUTURE), FUTURE)
        api.dispatch((command,), entry=entry)

        assert update_light.call_args_list == [call(orc.Light.c, on=True)]

    def test_snapshot_bypassed(self, update_light, snapshot_config, entry):
        command = em.Command(m.Devices(orc.Light.c), m.ON)

        api._ctx.engine.save_snapshot(api.ORC_SYSTEM_SNAPSHOT, m.SnapShot(routine=snapshot_config, end=FUTURE), FUTURE)

        api.dispatch((command,), force=True, entry=entry)

        assert api._ctx.engine.snapshots()[api.ORC_SYSTEM_SNAPSHOT].routine == (
            em.Command(m.Devices(orc.Light.a), m.ON),
            em.Command(m.Devices(orc.Light.b), m.OFF),
        )
        assert update_light.call_args_list == [call(orc.Light.c, on=True)]

    def test_force_off_is_not_recorded_and_resume_relights(self, update_light, snapshot_config, entry):
        api._ctx.engine.save_snapshot(api.ORC_SYSTEM_SNAPSHOT, m.SnapShot(routine=snapshot_config, end=FUTURE), FUTURE)

        api.dispatch((em.Command(m.Devices(orc.Light.a), m.OFF),), force=True, entry=entry)  # room control during the scene

        assert api._ctx.engine.snapshots()[api.ORC_SYSTEM_SNAPSHOT].routine == (
            em.Command(m.Devices(orc.Light.a), m.ON),
            em.Command(m.Devices(orc.Light.b), m.OFF),
        )

        api.restore_scene(api.ORC_SYSTEM_SNAPSHOT, (), entry)

        assert update_light.call_args_list == [
            call(orc.Light.a, on=False),  # the deliberate off
            call(orc.Light.a, on=True),  # resume undoes it
            call(orc.Light.b, on=False),
        ]


def test_dispatch_usb_sets_volume(entry):
    from orc.dal.audio import stub as audio_stub

    api.dispatch((em.Command(m.Devices(orc.USB.speaker), 50),), force=True, entry=entry)

    assert audio_stub._volumes == {orc.USB.speaker: 50}


def test_dispatch_usb_plays_alert_path(entry):
    from orc.dal.audio import stub as audio_stub

    api.dispatch((em.Command(m.Devices(orc.USB.speaker), "/tmp/alert.wav"),), force=True, entry=entry)

    assert audio_stub._alerted == ["/tmp/alert.wav"]


def test_back_on_schedule_checks_presence_then_replays(entry):
    ctx = MagicMock()
    ctx.api = create_autospec(api)
    plugins.back_on_schedule(ctx, None, entry=entry)
    ctx.api.check_presence.assert_called_once_with(entry.trigger)
    ctx.api.replay_day.assert_called_once_with(ctx.api.local_now.return_value, entry)


def test_button_ad_hoc_snapshot(ctx, dispatched):
    routine = m.AdhocAction((em.Command(m.Devices(orc.Light.b), m.ON),), snapshot=timedelta(hours=3))
    captured = (em.Command(m.Devices(orc.Light.a), m.ON),)
    with (
        patch.multiple(config, plugins={}, schedule_routines={}, ad_hoc_routines={"r": routine}),
        patch.object(api, "capture_lights", return_value=captured),
    ):
        api.run_action(ctx, "r", m.Button("1"), source=m.LogSource.EXTERNAL)
    snap = ctx.engine.snapshots()[api.ORC_SYSTEM_SNAPSHOT]
    assert snap.routine is captured
    assert snap.end > api.local_now()
    dispatched.assert_called_once_with(routine.commands, force=True, entry=ANY)


def test_button_ad_hoc_snapshot_does_not_stack(ctx, dispatched):
    routine = m.AdhocAction((em.Command(m.Devices(orc.Light.b), m.ON),), snapshot=timedelta(hours=3))
    reset = _routine("reset", "", em.Command(m.Devices(orc.Light.a), m.OFF))
    existing = (em.Command(m.Devices(orc.Light.a), m.ON),)
    snap = m.SnapShot(routine=existing, end=api.local_now() + timedelta(hours=1))
    ctx.engine.save_snapshot(api.ORC_SYSTEM_SNAPSHOT, snap, snap.end)
    with (
        patch.multiple(config, plugins={}, schedule_routines={}, ad_hoc_routines={"r": routine}, reset_config=reset),
        patch.object(api, "capture_lights") as capture,
    ):
        api.run_action(ctx, "r", m.Button("1"), source=m.LogSource.EXTERNAL)
    # Existing snapshot is preserved (not popped, not overwritten) and no new one is taken.
    assert ctx.engine.snapshots()[api.ORC_SYSTEM_SNAPSHOT].routine is existing
    capture.assert_not_called()
    dispatched.assert_called_once_with((*reset.commands, *routine.commands), force=True, entry=ANY)


def test_dispatch_routes_ac_commands(entry, ac):
    api.dispatch((em.Command(m.Devices(orc.AC.unit), m.AcCommand(m.AcMode.COOL, "low", 75)),), force=True, entry=entry)
    api.dispatch((em.Command(m.Devices(orc.AC.unit), m.ON),), force=True, entry=entry)
    api.dispatch((em.Command(m.Devices(orc.AC.unit), m.OFF),), force=True, entry=entry)

    assert ac.command.call_args_list == [
        call(orc.AC.unit, m.ON, m.AcMode.COOL, "low", 75),
        call(orc.AC.unit, m.ON, None, None, None),
        call(orc.AC.unit, m.OFF, None, None, None),
    ]


def test_capture_acs_reads_each_device_through_the_handler(ac):
    ac.state.return_value = m.AcState.COOL
    assert api.capture_acs() == (m.AcStatus(orc.AC.unit, m.AcState.COOL),)
    api.set_ac(None)
    assert api.capture_acs() == (m.AcStatus(orc.AC.unit, None),)


def test_capture_acs_carries_the_setpoint_when_a_handler_supplies_one(ac):
    ac.state.return_value = m.AcState.COOL
    ac.temperature.return_value = 72
    assert api.capture_acs() == (m.AcStatus(orc.AC.unit, m.AcState.COOL, 72),)


def test_capture_sensors_reads_the_device_cache():
    device = m.DeviceState(id=orc.Sensor.living.value, name="living room sensor", attributes={"temperature": 70}, last_activity=None)
    with patch.object(mqtt_stub, "snapshot", return_value=[device]):
        assert api.capture_sensors() == [m.DeviceStatus(name="living room sensor", details={"temperature": 70})]


def test_capture_sensors_lists_sensors_missing_from_the_cache():
    assert api.capture_sensors() == [m.DeviceStatus(name=orc.Sensor.living.name, details={})]


def test_dispatch_usb_rejects_on_off_state(entry):
    from orc.dal.audio import stub as audio_stub

    api.dispatch((em.Command(m.Devices(orc.USB.speaker), m.ON),), force=True, entry=entry)

    assert len(audio_stub._spoken) == 1
    assert "USB devices don't support state" in audio_stub._spoken[0]


class TestLog:
    @pytest.fixture(autouse=True)
    def _clear(self):
        api._ACTIVITY_LOG.clear()

    def test_a_burst_from_one_trigger_nests_under_the_entry_it_opened(self):
        api.log(m.LogSource.PLUGIN, "first", m.Integration("x"))
        api.log(m.LogSource.PLUGIN, "second", m.Integration("x"))
        entries = api.log_entries()
        assert [e.action for e in entries] == ["first"]
        assert [c.action for c in entries[0].children] == ["second"]

    def test_a_device_report_nests_under_the_entry_that_requested_it(self):
        requester = api.log(m.LogSource.PLUGIN, "react", m.Integration("sensor"))
        api.dispatch((em.Command(m.Devices(orc.USB.speaker), 3),), force=True, entry=requester)
        api.log(m.LogSource.PLUGIN, "report", m.Broker(id=str(orc.USB.speaker.value), source="usb", value=3))
        assert [c.action for c in requester.children] == ["report"]
        assert [e.action for e in api.log_entries()] == ["react"]
        assert requester.requests == ()

    def test_a_bare_on_is_answered_by_any_powered_state(self, ac):
        requester = api.log(m.LogSource.PLUGIN, "react", m.Integration("sensor"))
        api.dispatch((em.Command(m.Devices(orc.AC.unit), m.ON),), force=True, entry=requester)
        api.log(
            m.LogSource.PLUGIN,
            "report",
            m.Broker(id=str(orc.AC.unit.value), source="lg_ac", value=m.AcCommand(m.AcMode.COOL, "low", 72)),
        )
        assert [c.action for c in requester.children] == ["report"]
        assert requester.requests == ()

    def test_a_device_report_that_differs_from_the_request_starts_its_own_entry(self):
        requester = api.log(m.LogSource.PLUGIN, "react", m.Integration("sensor"))
        api.dispatch((em.Command(m.Devices(orc.USB.speaker), 3),), force=True, entry=requester)
        api.log(m.LogSource.PLUGIN, "report", m.Broker(id=str(orc.USB.speaker.value), source="usb", value=4))
        assert requester.children == []
        assert requester.requests == (m.Request(str(orc.USB.speaker.value), 3),)
        assert [e.action for e in api.log_entries()] == ["report", "react"]

    def test_a_device_report_long_after_the_request_starts_its_own_entry(self):
        requester = api.log(m.LogSource.PLUGIN, "react", m.Integration("sensor"))
        api.dispatch((em.Command(m.Devices(orc.USB.speaker), 3),), force=True, entry=requester)
        with freeze_time(api.local_now() + api._ROLLUP_WINDOW):
            api.log(m.LogSource.PLUGIN, "report", m.Broker(id=str(orc.USB.speaker.value), source="usb", value=3))
        assert requester.children == []
        assert [e.action for e in api.log_entries()] == ["report", "react"]

    def test_the_newest_of_two_equal_requests_takes_the_report(self):
        older = api.log(m.LogSource.PLUGIN, "older", m.Integration("a"))
        api.dispatch((em.Command(m.Devices(orc.USB.speaker), 3),), force=True, entry=older)
        newer = api.log(m.LogSource.PLUGIN, "newer", m.Integration("b"))
        api.dispatch((em.Command(m.Devices(orc.USB.speaker), 3),), force=True, entry=newer)
        api.log(m.LogSource.PLUGIN, "report", m.Broker(id=str(orc.USB.speaker.value), source="usb", value=3))
        assert [c.action for c in newer.children] == ["report"]
        assert older.children == [] and older.requests == (m.Request(str(orc.USB.speaker.value), 3),)

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

    def test_a_greeted_subscription_is_pushed_alone(self, push_provider):
        subscriptions = [m.PushSubscription(f"https://push.example/{name}", "public-key", "auth-secret") for name in "ab"]
        api.subscribe_push(subscriptions[0])
        with freeze_time(datetime(2026, 1, 5, 12, tzinfo=config.settings.tz)):
            api.subscribe_push(subscriptions[1], greet=True)
        now = api._ctx.scheduler.now
        assert now.call_args.args[0] is api._push_job
        assert now.call_args.args[1:] == (
            "[System 01/05]",
            "Notifications enabled on this device",
            m.Notification(("greeting",)),
            m.Manual("notify"),
            (subscriptions[1],),
        )
        assert set(sqlite.fetch_push_subscriptions()) == set(subscriptions)

    def test_unsubscribing_drops_only_that_endpoint(self):
        subscriptions = [m.PushSubscription(f"https://push.example/{name}", "public-key", "auth-secret") for name in "ab"]
        for subscription in subscriptions:
            api.subscribe_push(subscription)
        api.unsubscribe_push(subscriptions[0].endpoint)
        assert sqlite.fetch_push_subscriptions() == [subscriptions[1]]

    def test_a_push_reaches_every_subscription_and_drops_gone_ones(self, push_provider):
        subscriptions = [m.PushSubscription(f"https://push.example/{name}", "public-key", "auth-secret") for name in "abc"]
        for subscription in subscriptions:
            api.subscribe_push(subscription)
        push_provider.send.side_effect = [None, push.Gone("b"), RuntimeError("boom")]
        entry = api.log(m.LogSource.PLUGIN, "Leak at `kitchen`", m.Integration("x"), notification=m.Notification(("leak",)))
        api._push_job("[Plugin 01/05]", "Leak at kitchen", "leak", entry.trigger, None, ctx=api._ctx)
        assert push_provider.send.call_args_list == [call(s, "[Plugin 01/05]", "Leak at kitchen", "leak") for s in subscriptions]
        assert set(sqlite.fetch_push_subscriptions()) == {subscriptions[0], subscriptions[2]}
        assert [c.action for c in entry.children] == ["Push failed for `…xample/c`: boom"]


@freeze_time(datetime(2026, 1, 5, 12, tzinfo=config.settings.tz))
class TestActiveOverride:
    OVERRIDE = m.ThemeOverride("vacation", date(2026, 1, 1), date(2026, 1, 10))

    @pytest.fixture(autouse=True)
    def _setup(self):
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
        return m.Theme(name, tuple(m.ThemeEntry(time(8, 0), _routine(n, time(8, 0))) for n in routine_names))

    @pytest.fixture(autouse=True)
    def _setup(self):
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
        return [routine.name for _, routine in schedule]

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

    def test_stale_entry_outside_12h_window(self):
        api.mark_present(["Alice"], datetime(2026, 1, 4, 23, 30, tzinfo=config.settings.tz), TRIGGER)
        assert api.present_names() == set()

    def test_run_iot_job_skips_when_presence_absent(self, dispatched):
        rule = self._routine("partner-r", "Alice")
        api.run_iot_job(m.IotJob(rule), ctx=self.ctx)
        dispatched.assert_not_called()

    def test_run_iot_job_runs_when_presence_present(self, dispatched):
        api.mark_present(["Alice"], api.local_now(), TRIGGER)
        rule = self._routine("partner-r", "Alice")
        api.run_iot_job(m.IotJob(rule), ctx=self.ctx)
        dispatched.assert_called_once_with(m.squish((em.Command(m.Devices(orc.Light.a), m.OFF, tag="Alice"),)), force=False, entry=ANY)

    def test_run_iot_job_runs_when_no_presence_required(self, dispatched):
        rule = _routine("r", time(8, 0), em.Command(m.Devices(orc.Light.a), m.OFF))
        api.run_iot_job(m.IotJob(rule), ctx=self.ctx)
        dispatched.assert_called_once_with(m.squish((em.Command(m.Devices(orc.Light.a), m.OFF),)), force=False, entry=ANY)

    def test_run_iot_job_system_trigger_bypasses_presence(self, dispatched):
        rule = self._routine("reset-r", m.Tag.SYSTEM)
        api.run_iot_job(m.IotJob(rule), ctx=self.ctx)
        dispatched.assert_called_once_with(m.squish((em.Command(m.Devices(orc.Light.a), m.OFF, tag=m.Tag.SYSTEM),)), force=False, entry=ANY)

    def test_run_iot_job_anyone_trigger_runs_when_someone_present(self, dispatched):
        api.mark_present(["Bob"], api.local_now(), TRIGGER)
        rule = self._routine("anyone-r", m.Tag.ANYONE)
        api.run_iot_job(m.IotJob(rule), ctx=self.ctx)
        dispatched.assert_called_once_with(m.squish((em.Command(m.Devices(orc.Light.a), m.OFF, tag=m.Tag.ANYONE),)), force=False, entry=ANY)

    def test_run_iot_job_anyone_trigger_skips_when_no_one_present(self, dispatched):
        rule = self._routine("anyone-r", m.Tag.ANYONE)
        api.run_iot_job(m.IotJob(rule), ctx=self.ctx)
        dispatched.assert_not_called()

    def test_run_iot_job_skip_log_blames_absence_not_weather(self, dispatched):
        rule = self._routine("sunny-r", "SUNNY")
        api.run_iot_job(m.IotJob(rule), ctx=self.ctx)
        dispatched.assert_not_called()
        assert "nobody home" in api.log_entries()[0].action

    def test_run_iot_job_skip_log_lists_weather_when_someone_home(self, dispatched):
        api.mark_present(["Alice"], api.local_now(), TRIGGER)
        rule = self._routine("cloudy-r", "CLOUDY")
        api.run_iot_job(m.IotJob(rule), ctx=self.ctx)
        dispatched.assert_not_called()
        assert "CLOUDY" in api.log_entries()[0].action

    def test_replay_day_skips_routines_for_absent_people(self, entry, dispatched):
        past = datetime(2026, 1, 5, 8, tzinfo=config.settings.tz)
        partner = self._routine("partner-r", "Alice")
        with patch.object(api, "get_schedule", return_value=[(past, partner)]):
            api.replay_day(api.local_now(), entry)
        dispatched.assert_called_once_with((), force=True, entry=entry)

    def test_replay_day_runs_routines_for_present_people(self, entry, dispatched):
        api.mark_present(["Alice"], api.local_now(), TRIGGER)
        past = datetime(2026, 1, 5, 8, tzinfo=config.settings.tz)
        partner = self._routine("partner-r", "Alice")
        with patch.object(api, "get_schedule", return_value=[(past, partner)]):
            api.replay_day(api.local_now(), entry)
        squished = dispatched.call_args.args[0]
        assert [(c.subject.one(), c.value) for c in squished] == [(orc.Light.a, m.OFF)]

    def test_replay_day_skips_skip_replay_routines(self, entry, dispatched):
        api.mark_present(["Alice"], api.local_now(), TRIGGER)
        past = datetime(2026, 1, 5, 8, tzinfo=config.settings.tz)
        meeting = replace(self._routine("meeting-r", "Alice"), tags=frozenset({m.SKIP_REPLAY_TAG}))
        with patch.object(api, "get_schedule", return_value=[(past, meeting)]):
            api.replay_day(api.local_now(), entry)
        dispatched.assert_called_once_with((), force=True, entry=entry)

    def test_check_presence_continues_when_one_host_fails_to_resolve(self):
        with patch.object(config, "people", {"Alice": {("alice.local", "aa:aa:aa:aa:aa:aa")}, "Bob": {("bob.local", "bb:bb:bb:bb:bb:bb")}}):

            def resolve(host):
                if host == "alice.local":
                    raise RuntimeError("dns boom")
                return "10.0.0.2"

            class FakeSniffer:
                def __init__(self, *a, **k):
                    self.results = [net.Ether() / net.ARP(op=2, psrc="10.0.0.2")]

                def start(self): ...
                def join(self, *a, **k): ...

            with (
                patch.object(net.socket, "gethostbyname", side_effect=resolve),
                patch.object(net, "AsyncSniffer", FakeSniffer),
                patch.object(net, "sendp"),
            ):
                api.check_presence(TRIGGER)
        assert api.present_names() == {"Bob"}

    def test_check_presence_with_only_tags_reads_memory(self):
        with patch.object(config, "people", {}), patch.object(config, "ble_tags", {"Alice": m.BleKey(bytes(32), 0)}):
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
    def _reported(entry):
        return [entry.action, *(c.action for c in entry.children)]

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
            patch.object(config, "ble_tags", {name: m.BleKey(bytes(32), 0) for name in ("Alice", "Bob", "Carol")}),
            patch.object(net.presence, "probe") as probe,
            patch.object(net, "scan_presence", return_value=(set(), [])),
        ):
            api.check_presence(TRIGGER)
            probe.assert_called_once_with({"Alice"}, TRIGGER)
            probe.reset_mock()
            api.check_presence(TRIGGER, probe=True)
        probe.assert_called_once_with({"Alice", "Carol"}, TRIGGER)


def test_context_executor_copies_closure_job():
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
