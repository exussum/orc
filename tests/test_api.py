from dataclasses import replace
from datetime import date, datetime, time, timedelta
from unittest.mock import ANY, call, patch

import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from freezegun import freeze_time

import orc
from orc import api, config
from orc import model as m
from orc.dal import net, scheduler
from orc.dal.mqtt import stub as mqtt_stub
from orc.kernel import engine, loader

FUTURE = datetime(2100, 1, 1, tzinfo=config.settings.tz)
PAST = datetime(2000, 1, 1, tzinfo=config.settings.tz)


def _routine(name, when, *commands, skip_replay=False):
    clauses = tuple(engine.Clause(loader._conditions(c.tag), c) for c in commands)
    tags = frozenset({m.SKIP_REPLAY_TAG}) if skip_replay else frozenset()
    return engine.Rule(engine.At(when), clauses, name=name, tags=tags)


@pytest.fixture
def snapshot_config():
    return (engine.Command(m.Devices(orc.Light.a), m.ON), engine.Command(m.Devices(orc.Light.b), m.OFF))


@pytest.fixture
def entry():
    return m.LogEntry(FUTURE, m.LogSource.MANUAL, "test")


@patch("orc.api.dispatch")
class TestManagingConfig:
    def test_resume_with_snapshot(self, dispatch, snapshot_config, entry):
        api._ctx.engine.save_snapshot("test", m.SnapShot(routine=snapshot_config, end=FUTURE), FUTURE)
        api._ctx.engine.restore_scene(api._ctx, "test", (), entry)
        assert dispatch.call_args_list == [call(snapshot_config, force=True, entry=entry)]

    def test_resume_without_snapshot(self, dispatch, snapshot_config, entry):
        api._ctx.engine.restore_scene(api._ctx, "test", snapshot_config, entry)
        assert dispatch.call_args_list == [call(snapshot_config, force=True, entry=entry)]

    def test_resume_with_old_snapshot(self, dispatch, snapshot_config, entry):
        api._ctx.engine.save_snapshot("test", m.SnapShot(routine=snapshot_config, end=PAST), PAST)
        api._ctx.engine.restore_scene(api._ctx, "test", snapshot_config, entry)
        assert dispatch.call_args_list == [call(snapshot_config, force=True, entry=entry)]
        assert not api._ctx.engine.snapshots(api.local_now())

    def test_get_with_snapshot(self, dispatch, snapshot_config):
        api._ctx.engine.save_snapshot("test", m.SnapShot(routine=snapshot_config, end=FUTURE), FUTURE)
        assert api._ctx.engine.pop_snapshot("test", api.local_now()).routine is snapshot_config
        assert not api._ctx.engine.snapshots(api.local_now())
        dispatch.assert_not_called()

    def test_get_without_snapshot(self, dispatch):
        assert api._ctx.engine.pop_snapshot("test", api.local_now()) is None
        dispatch.assert_not_called()

    def test_get_with_old_snapshot(self, dispatch, snapshot_config):
        api._ctx.engine.save_snapshot("test", m.SnapShot(routine=snapshot_config, end=PAST), PAST)
        assert api._ctx.engine.pop_snapshot("test", api.local_now()) is None
        assert not api._ctx.engine.snapshots(api.local_now())
        dispatch.assert_not_called()


@patch("orc.dal.mqtt.stub.publish_light")
class TestIntercepts:
    def test_snapshot_update_overwrite_set(self, update_light, snapshot_config, entry):
        command = engine.Command(m.Devices(orc.Light.b), m.ON, tag=m.Trigger.SYSTEM)

        api._ctx.engine.save_snapshot(api.ORC_SYSTEM_SNAPSHOT, m.SnapShot(routine=snapshot_config, end=FUTURE), FUTURE)
        api.dispatch((command,), entry=entry)
        api.dispatch((command,), entry=entry)

        assert api._ctx.engine.snapshots(api.local_now())[api.ORC_SYSTEM_SNAPSHOT].routine == (
            engine.Command(m.Devices(orc.Light.a), m.ON),
            engine.Command(m.Devices(orc.Light.b), m.ON, tag=m.Trigger.SYSTEM),
        )
        assert update_light.call_args_list == [call(orc.Light.b, on=True), call(orc.Light.b, on=True)]

    def test_snapshot_update_add(self, update_light, snapshot_config, entry):
        command = engine.Command(m.Devices(orc.Light.c), m.ON, tag=m.Trigger.SYSTEM)

        api._ctx.engine.save_snapshot(api.ORC_SYSTEM_SNAPSHOT, m.SnapShot(routine=snapshot_config, end=FUTURE), FUTURE)
        api.dispatch((command,), entry=entry)

        assert api._ctx.engine.snapshots(api.local_now())[api.ORC_SYSTEM_SNAPSHOT].routine == (
            engine.Command(m.Devices(orc.Light.a), m.ON),
            engine.Command(m.Devices(orc.Light.b), m.OFF),
            command,
        )
        assert update_light.call_args_list == [call(orc.Light.c, on=True)]

    def test_rule_ignored(self, update_light, snapshot_config, entry):
        command = engine.Command(m.Devices(orc.Light.c), m.ON)

        api._ctx.engine.save_snapshot(api.ORC_SYSTEM_SNAPSHOT, m.SnapShot(routine=snapshot_config, end=FUTURE), FUTURE)
        api.dispatch((command,), entry=entry)

        assert api._ctx.engine.snapshots(api.local_now())[api.ORC_SYSTEM_SNAPSHOT].routine == (
            engine.Command(m.Devices(orc.Light.a), m.ON),
            engine.Command(m.Devices(orc.Light.b), m.OFF),
        )
        assert update_light.call_args_list == []

    def test_rule_old_snapshot(self, update_light, snapshot_config, entry):
        command = engine.Command(m.Devices(orc.Light.c), m.ON)

        api._ctx.engine.save_snapshot(api.ORC_SYSTEM_SNAPSHOT, m.SnapShot(routine=snapshot_config, end=PAST), PAST)
        api.dispatch((command,), entry=entry)

        assert not api._ctx.engine.snapshots(api.local_now())
        assert update_light.call_args_list == [call(orc.Light.c, on=True)]

    def test_unrelated_plugin_snapshot_does_not_suppress(self, update_light, snapshot_config, entry):
        command = engine.Command(m.Devices(orc.Light.c), m.ON)

        api._ctx.engine.save_snapshot("entrance_sensor", m.SnapShot(routine=snapshot_config, end=FUTURE), FUTURE)
        api.dispatch((command,), entry=entry)

        assert update_light.call_args_list == [call(orc.Light.c, on=True)]

    def test_snapshot_bypassed(self, update_light, snapshot_config, entry):
        command = engine.Command(m.Devices(orc.Light.c), m.ON)

        api._ctx.engine.save_snapshot(api.ORC_SYSTEM_SNAPSHOT, m.SnapShot(routine=snapshot_config, end=FUTURE), FUTURE)

        api.dispatch((command,), force=True, entry=entry)

        assert api._ctx.engine.snapshots(api.local_now())[api.ORC_SYSTEM_SNAPSHOT].routine == (
            engine.Command(m.Devices(orc.Light.a), m.ON),
            engine.Command(m.Devices(orc.Light.b), m.OFF),
        )
        assert update_light.call_args_list == [call(orc.Light.c, on=True)]

    def test_force_off_is_not_recorded_and_resume_relights(self, update_light, snapshot_config, entry):
        api._ctx.engine.save_snapshot(api.ORC_SYSTEM_SNAPSHOT, m.SnapShot(routine=snapshot_config, end=FUTURE), FUTURE)

        api.dispatch((engine.Command(m.Devices(orc.Light.a), m.OFF),), force=True, entry=entry)  # room control during the scene

        assert api._ctx.engine.snapshots(api.local_now())[api.ORC_SYSTEM_SNAPSHOT].routine == (
            engine.Command(m.Devices(orc.Light.a), m.ON),
            engine.Command(m.Devices(orc.Light.b), m.OFF),
        )

        api._ctx.engine.restore_scene(api._ctx, api.ORC_SYSTEM_SNAPSHOT, (), entry)

        assert update_light.call_args_list == [
            call(orc.Light.a, on=False),  # the deliberate off
            call(orc.Light.a, on=True),  # resume undoes it
            call(orc.Light.b, on=False),
        ]


def test_dispatch_usb_sets_volume(entry):
    from orc.dal.audio import stub as audio_stub

    api.dispatch((engine.Command(m.Devices(orc.USB.speaker), 50),), force=True, entry=entry)

    assert audio_stub._volumes == {orc.USB.speaker: 50}


def test_dispatch_usb_plays_alert_path(entry):
    from orc.dal.audio import stub as audio_stub

    api.dispatch((engine.Command(m.Devices(orc.USB.speaker), "/tmp/alert.wav"),), force=True, entry=entry)

    assert audio_stub._alerted == ["/tmp/alert.wav"]


def test_dispatch_routes_ac_commands(entry):
    with patch.object(config.registry, "ac_handler") as handler:
        api.dispatch((engine.Command(m.Devices(orc.AC.unit), m.AcCommand(m.AcMode.COOL, "low", 75)),), force=True, entry=entry)
        api.dispatch((engine.Command(m.Devices(orc.AC.unit), m.ON),), force=True, entry=entry)
        api.dispatch((engine.Command(m.Devices(orc.AC.unit), m.OFF),), force=True, entry=entry)

    assert handler.call_args_list == [
        call(orc.AC.unit, m.ON, m.AcMode.COOL, "low", 75),
        call(orc.AC.unit, m.ON, None, None, None),
        call(orc.AC.unit, m.OFF, None, None, None),
    ]


def test_capture_acs_reads_each_device_through_the_handler():
    with patch.object(config.registry, "ac_state_handler", lambda device: m.AcState.COOL):
        assert api.capture_acs() == (m.AcStatus(orc.AC.unit, m.AcState.COOL),)
    assert api.capture_acs() == (m.AcStatus(orc.AC.unit, None),)


def test_capture_sensors_reads_the_device_cache():
    device = m.DeviceState(id=orc.Sensor.living.value, name="living room sensor", attributes={"temperature": 70}, last_activity=None)
    with patch.object(mqtt_stub, "snapshot", return_value=[device]):
        assert api.capture_sensors() == [m.DeviceStatus(name="living room sensor", details={"temperature": 70})]


def test_capture_sensors_lists_sensors_missing_from_the_cache():
    with patch.object(mqtt_stub, "snapshot", return_value=[]):
        assert api.capture_sensors() == [m.DeviceStatus(name=orc.Sensor.living.name, details={})]


def test_dispatch_usb_rejects_on_off_state(entry):
    from orc.dal.audio import stub as audio_stub

    api.dispatch((engine.Command(m.Devices(orc.USB.speaker), m.ON),), force=True, entry=entry)

    assert len(audio_stub._spoken) == 1
    assert "USB devices don't support state" in audio_stub._spoken[0]


class TestLog:
    @pytest.fixture(autouse=True)
    def _clear(self):
        api._ACTIVITY_LOG.clear()

    def test_amend_nests_under_the_same_source(self):
        api.log(m.LogSource.PLUGIN, "first")
        api.log(m.LogSource.PLUGIN, "second", amend=True)
        entries = api.log_entries()
        assert [e.action for e in entries] == ["first"]
        assert [c.action for c in entries[0].children] == ["second"]

    def test_amend_starts_a_new_entry_for_a_different_source(self):
        api.log(m.LogSource.SYSTEM, "sys")
        api.log(m.LogSource.PLUGIN, "plug", amend=True)
        entries = api.log_entries()
        assert [e.action for e in entries] == ["plug", "sys"]
        assert entries[0].children == []

    def test_amend_on_an_empty_log_creates_a_top_level_entry(self):
        api.log(m.LogSource.PLUGIN, "only", amend=True)
        assert [e.action for e in api.log_entries()] == ["only"]


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


# 2026-01-03 is Saturday, 2026-01-04 is Sunday
@freeze_time(datetime(2026, 1, 3, 12, tzinfo=config.settings.tz))
class TestGetSchedule:
    @staticmethod
    def _theme(name, *routine_names):
        return m.Theme(name, *(_routine(n, time(8, 0)) for n in routine_names))

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
        return _routine(name, time(8, 0), engine.Command(m.Devices(orc.Light.a), m.OFF, tag=trigger))

    def test_mark_and_query(self):
        assert api.present_names() == set()
        api.mark_present(["Alice"], when=api.local_now())
        assert api.present_names() == {"Alice"}

    def test_expire(self):
        api.mark_present(["Alice"], when=api.local_now() - timedelta(minutes=1))
        api.expire_presence(["Alice"])
        assert api.present_names() == set()

    def test_stale_entry_outside_12h_window(self):
        api.mark_present(["Alice"], when=datetime(2026, 1, 4, 23, 30, tzinfo=config.settings.tz))
        assert api.present_names() == set()

    def test_run_iot_job_skips_when_presence_absent(self):
        rule = self._routine("partner-r", "Alice")
        with patch.object(api, "dispatch") as dispatch:
            api.run_iot_job(m.IotJob(rule), ctx=self.ctx)
        dispatch.assert_not_called()

    def test_run_iot_job_runs_when_presence_present(self):
        api.mark_present(["Alice"], when=api.local_now())
        rule = self._routine("partner-r", "Alice")
        with patch.object(api, "dispatch") as dispatch:
            api.run_iot_job(m.IotJob(rule), ctx=self.ctx)
        dispatch.assert_called_once_with(m.squish((engine.Command(m.Devices(orc.Light.a), m.OFF, tag="Alice"),)), force=False, entry=ANY)

    def test_run_iot_job_runs_when_no_presence_required(self):
        rule = _routine("r", time(8, 0), engine.Command(m.Devices(orc.Light.a), m.OFF))
        with patch.object(api, "dispatch") as dispatch:
            api.run_iot_job(m.IotJob(rule), ctx=self.ctx)
        dispatch.assert_called_once_with(m.squish((engine.Command(m.Devices(orc.Light.a), m.OFF),)), force=False, entry=ANY)

    def test_run_iot_job_system_trigger_bypasses_presence(self):
        rule = self._routine("reset-r", m.Trigger.SYSTEM)
        with patch.object(api, "dispatch") as dispatch:
            api.run_iot_job(m.IotJob(rule), ctx=self.ctx)
        dispatch.assert_called_once_with(
            m.squish((engine.Command(m.Devices(orc.Light.a), m.OFF, tag=m.Trigger.SYSTEM),)), force=False, entry=ANY
        )

    def test_run_iot_job_anyone_trigger_runs_when_someone_present(self):
        api.mark_present(["Bob"], when=api.local_now())
        rule = self._routine("anyone-r", m.Trigger.ANYONE)
        with patch.object(api, "dispatch") as dispatch:
            api.run_iot_job(m.IotJob(rule), ctx=self.ctx)
        dispatch.assert_called_once_with(
            m.squish((engine.Command(m.Devices(orc.Light.a), m.OFF, tag=m.Trigger.ANYONE),)), force=False, entry=ANY
        )

    def test_run_iot_job_anyone_trigger_skips_when_no_one_present(self):
        rule = self._routine("anyone-r", m.Trigger.ANYONE)
        with patch.object(api, "dispatch") as dispatch:
            api.run_iot_job(m.IotJob(rule), ctx=self.ctx)
        dispatch.assert_not_called()

    def test_run_iot_job_skip_log_blames_absence_not_weather(self):
        rule = self._routine("sunny-r", "SUNNY")
        with patch.object(api, "dispatch") as dispatch:
            api.run_iot_job(m.IotJob(rule), ctx=self.ctx)
        dispatch.assert_not_called()
        assert "nobody home" in api.log_entries()[0].action

    def test_run_iot_job_skip_log_lists_weather_when_someone_home(self):
        api.mark_present(["Alice"], when=api.local_now())
        rule = self._routine("cloudy-r", "CLOUDY")
        with patch.object(api, "dispatch") as dispatch:
            api.run_iot_job(m.IotJob(rule), ctx=self.ctx)
        dispatch.assert_not_called()
        assert "CLOUDY" in api.log_entries()[0].action

    def test_replay_day_skips_routines_for_absent_people(self, entry):
        past = datetime(2026, 1, 5, 8, tzinfo=config.settings.tz)
        partner = self._routine("partner-r", "Alice")
        with patch.object(api, "get_schedule", return_value=[(past, partner)]), patch.object(api, "dispatch") as dispatch:
            api.replay_day(api.local_now(), entry)
        dispatch.assert_called_once_with((), force=True, entry=entry)

    def test_replay_day_runs_routines_for_present_people(self, entry):
        api.mark_present(["Alice"], when=api.local_now())
        past = datetime(2026, 1, 5, 8, tzinfo=config.settings.tz)
        partner = self._routine("partner-r", "Alice")
        with patch.object(api, "get_schedule", return_value=[(past, partner)]), patch.object(api, "dispatch") as dispatch:
            api.replay_day(api.local_now(), entry)
        squished = dispatch.call_args.args[0]
        assert [(c.channel.one(), c.value) for c in squished] == [(orc.Light.a, m.OFF)]

    def test_replay_day_skips_skip_replay_routines(self, entry):
        api.mark_present(["Alice"], when=api.local_now())
        past = datetime(2026, 1, 5, 8, tzinfo=config.settings.tz)
        meeting = replace(self._routine("meeting-r", "Alice"), tags=frozenset({m.SKIP_REPLAY_TAG}))
        with patch.object(api, "get_schedule", return_value=[(past, meeting)]), patch.object(api, "dispatch") as dispatch:
            api.replay_day(api.local_now(), entry)
        dispatch.assert_called_once_with((), force=True, entry=entry)

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
                api.check_presence()
        assert api.present_names() == {"Bob"}

    def test_check_presence_with_only_tags_reads_memory(self):
        with patch.object(config, "people", {}), patch.object(config, "ble_tags", {"Alice": m.BleKey(bytes(32), 0)}):
            api.mark_present(["Alice"], when=api.local_now())
            assert api.check_presence() == {"Alice"}

    def test_pause_hides_earlier_evidence_until_resume(self):
        api.mark_present(["Alice"], when=api.local_now() - timedelta(minutes=1))
        api.pause_presence()
        assert api.present_names() == set()
        api.resume_presence()
        assert api.present_names() == {"Alice"}

    def test_marks_during_pause_count(self):
        api.pause_presence()
        api.mark_present(["Alice"], when=api.local_now() + timedelta(seconds=1))
        assert api.present_names() == {"Alice"}

    def test_future_checkin_survives_pause_and_expiry(self):
        api.mark_present(["Alice"], when=api.local_now() + timedelta(hours=1))
        api.pause_presence()
        api.expire_presence(["Alice"])
        assert api.present_names() == {"Alice"}

    def test_force_expire_drops_future_checkin(self):
        api.mark_present(["Alice"], when=api.local_now() + timedelta(hours=1))
        api.expire_presence(["Alice"], force=True)
        assert api.present_names() == set()

    def test_mark_reports_detected_once(self):
        api.mark_present(["Alice"], when=api.local_now())
        first = api.log_entries()[0]
        assert "Presence detected: `Alice`" in first.action
        api.mark_present(["Alice"], when=api.local_now())
        assert api.log_entries()[0] is first

    def test_report_waits_for_resume(self):
        api.pause_presence()
        entries = len(api.log_entries())
        api.mark_present(["Alice"], when=api.local_now() + timedelta(seconds=1))
        assert len(api.log_entries()) == entries
        api.resume_presence()
        assert "Presence detected: `Alice`" in api.log_entries()[0].action

    def test_report_logs_lost_after_expiry(self):
        api.mark_present(["Alice"], when=api.local_now())
        api.expire_presence(["Alice"], force=True)
        assert "Presence lost: `Alice`" in api.log_entries()[0].action


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


class TestWireButtons:
    def _wire(self, buttons, run_result=True):
        from unittest.mock import MagicMock

        ctx = MagicMock()
        captured = {}
        with (
            patch.object(config, "remotes", buttons),
            patch.object(mqtt_stub, "add_button_listener", side_effect=lambda fn: captured.setdefault("fn", fn)),
        ):
            api.wire_buttons(ctx)
        return ctx, captured["fn"]

    def test_mapped_event_runs_action_as_hub_origin(self):
        ctx, on_button = self._wire((m.Remote(orc.Light.a, 1, "held", "TV Lights"),))
        with patch.object(api, "run_action", return_value=True) as run:
            on_button(orc.Light.a.value, 1, "held")
        run.assert_called_once_with(ctx, "TV Lights", hub_origin=True)

    def test_unmapped_event_is_ignored(self):
        ctx, on_button = self._wire((m.Remote(orc.Light.a, 1, "held", "TV Lights"),))
        with patch.object(api, "run_action") as run:
            on_button(99, 1, "held")
            on_button(orc.Light.a.value, 2, "held")
            on_button(orc.Light.a.value, 1, "pushed")
        run.assert_not_called()

    def test_unknown_action_logs(self):
        ctx, on_button = self._wire((m.Remote(orc.Light.a, 1, "held", "No Such Routine"),))
        with patch.object(api, "run_action", return_value=False), patch.object(api, "log") as log:
            on_button(orc.Light.a.value, 1, "held")
        log.assert_called_once()
        assert "No Such Routine" in log.call_args[0][1]
