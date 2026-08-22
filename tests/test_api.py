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

FUTURE = datetime(2100, 1, 1, tzinfo=config.settings.tz)
PAST = datetime(2000, 1, 1, tzinfo=config.settings.tz)


@pytest.fixture
def snapshot_config():
    return m.Configs(m.Config(orc.Light.a, m.ON), m.Config(orc.Light.b, m.OFF))


@pytest.fixture
def entry():
    return m.LogEntry(FUTURE, m.LogSource.MANUAL, "test")


@patch("orc.api.dispatch")
class TestManagingConfig:
    def setup_method(self):
        self.target = api.SnapshotManager()

    def test_resume_with_snapshot(self, dispatch, snapshot_config, entry):
        self.target.snapshots["test"] = m.SnapShot(routine=snapshot_config, end=FUTURE)
        self.target.resume("test", None, entry)
        assert dispatch.call_args_list == [call(snapshot_config, force=True, entry=entry)]

    def test_resume_without_snapshot(self, dispatch, entry):
        routine = object()
        self.target.resume("test", routine, entry)
        assert dispatch.call_args_list == [call(routine, force=True, entry=entry)]

    def test_resume_with_old_snapshot(self, dispatch, snapshot_config, entry):
        routine = object()
        self.target.snapshots["test"] = m.SnapShot(routine=snapshot_config, end=PAST)
        self.target.resume("test", routine, entry)
        assert dispatch.call_args_list == [call(routine, force=True, entry=entry)]
        assert not self.target.snapshots

    def test_get_with_snapshot(self, dispatch, snapshot_config):
        self.target.snapshots["test"] = m.SnapShot(routine=snapshot_config, end=FUTURE)
        assert self.target.get("test").routine is snapshot_config
        assert not self.target.snapshots
        dispatch.assert_not_called()

    def test_get_without_snapshot(self, dispatch):
        assert self.target.get("test") is None
        dispatch.assert_not_called()

    def test_get_with_old_snapshot(self, dispatch, snapshot_config):
        self.target.snapshots["test"] = m.SnapShot(routine=snapshot_config, end=PAST)
        assert self.target.get("test") is None
        assert not self.target.snapshots
        dispatch.assert_not_called()


@patch("orc.dal.mqtt.stub.publish_light")
class TestIntercepts:
    @pytest.fixture(autouse=True)
    def _manager(self):
        self.target = api.SnapshotManager()
        with patch.object(api, "snapshot_manager", self.target):
            yield

    def test_snapshot_update_overwrite_set(self, update_light, snapshot_config, entry):
        rule = m.Config(set((orc.Light.b,)), m.ON, trigger=m.Trigger.SYSTEM)

        self.target.snapshots[api.ORC_SYSTEM_SNAPSHOT] = m.SnapShot(routine=snapshot_config, end=FUTURE)
        api.dispatch(rule, entry=entry)
        api.dispatch(rule, entry=entry)

        assert self.target.snapshots[api.ORC_SYSTEM_SNAPSHOT].routine.items == (
            m.Config(orc.Light.a, m.ON),
            m.Config(orc.Light.b, m.ON, trigger=m.Trigger.SYSTEM),
        )
        assert update_light.call_args_list == [call(orc.Light.b, on=True), call(orc.Light.b, on=True)]

    def test_snapshot_update_add(self, update_light, snapshot_config, entry):
        rule = m.Config(orc.Light.c, m.ON, trigger=m.Trigger.SYSTEM)

        self.target.snapshots[api.ORC_SYSTEM_SNAPSHOT] = m.SnapShot(routine=snapshot_config, end=FUTURE)
        api.dispatch(rule, entry=entry)

        assert self.target.snapshots[api.ORC_SYSTEM_SNAPSHOT].routine.items == (
            m.Config(orc.Light.a, m.ON),
            m.Config(orc.Light.b, m.OFF),
            rule,
        )
        assert update_light.call_args_list == [call(orc.Light.c, on=True)]

    def test_rule_ignored(self, update_light, snapshot_config, entry):
        rule = m.Config(orc.Light.c, m.ON)

        self.target.snapshots[api.ORC_SYSTEM_SNAPSHOT] = m.SnapShot(routine=snapshot_config, end=FUTURE)
        api.dispatch(rule, entry=entry)

        assert self.target.snapshots[api.ORC_SYSTEM_SNAPSHOT].routine.items == (
            m.Config(orc.Light.a, m.ON),
            m.Config(orc.Light.b, m.OFF),
        )
        assert update_light.call_args_list == []

    def test_rule_old_snapshot(self, update_light, snapshot_config, entry):
        rule = m.Config(orc.Light.c, m.ON)

        self.target.snapshots[api.ORC_SYSTEM_SNAPSHOT] = m.SnapShot(routine=snapshot_config, end=PAST)
        api.dispatch(rule, entry=entry)

        assert not self.target.snapshots
        assert update_light.call_args_list == [call(orc.Light.c, on=True)]

    def test_snapshot_bypassed(self, update_light, snapshot_config, entry):
        rule = m.Config(orc.Light.c, m.ON)

        self.target.snapshots[api.ORC_SYSTEM_SNAPSHOT] = m.SnapShot(routine=snapshot_config, end=FUTURE)

        api.dispatch(rule, force=True, entry=entry)

        assert self.target.snapshots[api.ORC_SYSTEM_SNAPSHOT].routine.items == (
            m.Config(orc.Light.a, m.ON),
            m.Config(orc.Light.b, m.OFF),
        )
        assert update_light.call_args_list == [call(orc.Light.c, on=True)]

    def test_force_off_is_not_recorded_and_resume_relights(self, update_light, snapshot_config, entry):
        self.target.snapshots[api.ORC_SYSTEM_SNAPSHOT] = m.SnapShot(routine=snapshot_config, end=FUTURE)

        api.dispatch(m.Config(orc.Light.a, m.OFF), force=True, entry=entry)  # room control during the scene

        assert self.target.snapshots[api.ORC_SYSTEM_SNAPSHOT].routine.items == (
            m.Config(orc.Light.a, m.ON),  # snapshot still holds the captured ON
            m.Config(orc.Light.b, m.OFF),
        )

        self.target.resume(api.ORC_SYSTEM_SNAPSHOT, None, entry)

        assert update_light.call_args_list == [
            call(orc.Light.a, on=False),  # the deliberate off
            call(orc.Light.a, on=True),  # resume undoes it
            call(orc.Light.b, on=False),
        ]


def test_unwrapper_function_single_rule():
    calls = []
    rule = m.Config(orc.Light.a, m.ON)

    @api.unwrap_rule_container
    def target(e):
        calls.append(e)

    target(m.Config(orc.Light.a, m.ON))

    assert calls == [rule]


def test_unwrapper_function_routine(snapshot_config):
    calls = []

    @api.unwrap_rule_container
    def target(e):
        calls.append(e)

    target(snapshot_config)

    assert calls == list(snapshot_config.items)


def test_unwrapper_class_single_rule():
    calls = []
    rule = m.Config(orc.Light.a, m.ON)

    class Foo:
        @api.unwrap_rule_container
        def target(self, e):
            calls.append(e)

    Foo().target(m.Config(orc.Light.a, m.ON))

    assert calls == [rule]


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
        return m.Theme(name, *(m.Routine(n, time(8, 0), ()) for n in routine_names))

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
        return m.Routine(name, time(8, 0), (m.Config(orc.Light.a, m.OFF, trigger=trigger),))

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
        dispatch.assert_called_once_with(m.Configs(m.Config(orc.Light.a, m.OFF, trigger="Alice")), force=False, entry=ANY)

    def test_run_iot_job_runs_when_no_presence_required(self):
        rule = m.Routine("r", time(8, 0), (m.Config(orc.Light.a, m.OFF),))
        with patch.object(api, "dispatch") as dispatch:
            api.run_iot_job(m.IotJob(rule), ctx=self.ctx)
        dispatch.assert_called_once_with(m.Configs(m.Config(orc.Light.a, m.OFF)), force=False, entry=ANY)

    def test_run_iot_job_system_trigger_bypasses_presence(self):
        rule = self._routine("reset-r", m.Trigger.SYSTEM)
        with patch.object(api, "dispatch") as dispatch:
            api.run_iot_job(m.IotJob(rule), ctx=self.ctx)
        dispatch.assert_called_once_with(m.Configs(m.Config(orc.Light.a, m.OFF, trigger=m.Trigger.SYSTEM)), force=False, entry=ANY)

    def test_run_iot_job_anyone_trigger_runs_when_someone_present(self):
        api.mark_present(["Bob"], when=api.local_now())
        rule = self._routine("anyone-r", m.Trigger.ANYONE)
        with patch.object(api, "dispatch") as dispatch:
            api.run_iot_job(m.IotJob(rule), ctx=self.ctx)
        dispatch.assert_called_once_with(m.Configs(m.Config(orc.Light.a, m.OFF, trigger=m.Trigger.ANYONE)), force=False, entry=ANY)

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
        squished = dispatch.call_args.args[0]
        assert squished.items == ()

    def test_replay_day_runs_routines_for_present_people(self, entry):
        api.mark_present(["Alice"], when=api.local_now())
        past = datetime(2026, 1, 5, 8, tzinfo=config.settings.tz)
        partner = self._routine("partner-r", "Alice")
        with patch.object(api, "get_schedule", return_value=[(past, partner)]), patch.object(api, "dispatch") as dispatch:
            api.replay_day(api.local_now(), entry)
        squished = dispatch.call_args.args[0]
        assert [c.trigger for c in squished.items] == ["Alice"]

    def test_replay_day_skips_skip_replay_routines(self, entry):
        api.mark_present(["Alice"], when=api.local_now())
        past = datetime(2026, 1, 5, 8, tzinfo=config.settings.tz)
        meeting = replace(self._routine("meeting-r", "Alice"), skip_replay=True)
        with patch.object(api, "get_schedule", return_value=[(past, meeting)]), patch.object(api, "dispatch") as dispatch:
            api.replay_day(api.local_now(), entry)
        squished = dispatch.call_args.args[0]
        assert squished.items == ()

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
        ctx, on_button = self._wire({(orc.Light.a, 1, "held"): "TV Lights"})
        with patch.object(api, "run_action", return_value=True) as run:
            on_button(orc.Light.a.value, 1, "held")
        run.assert_called_once_with(ctx, "TV Lights", hub_origin=True)

    def test_unmapped_event_is_ignored(self):
        ctx, on_button = self._wire({(orc.Light.a, 1, "held"): "TV Lights"})
        with patch.object(api, "run_action") as run:
            on_button(99, 1, "held")
            on_button(orc.Light.a.value, 2, "held")
            on_button(orc.Light.a.value, 1, "pushed")
        run.assert_not_called()

    def test_unknown_action_logs(self):
        ctx, on_button = self._wire({(orc.Light.a, 1, "held"): "No Such Routine"})
        with patch.object(api, "run_action", return_value=False), patch.object(api, "log") as log:
            on_button(orc.Light.a.value, 1, "held")
        log.assert_called_once()
        assert "No Such Routine" in log.call_args[0][1]
