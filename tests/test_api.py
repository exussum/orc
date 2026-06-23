from datetime import date, datetime, time, timedelta
from unittest.mock import MagicMock, call, patch

import pytest
from freezegun import freeze_time

import orc
from orc import api, config
from orc import model as m

FUTURE = datetime(2100, 1, 1, tzinfo=config.tz)
PAST = datetime(2000, 1, 1, tzinfo=config.tz)


@pytest.fixture
def snapshot_config():
    return m.Configs(m.Config(orc.Light.a, config.ON), m.Config(orc.Light.b, config.OFF))


@patch("orc.api.execute")
class TestManagingConfig:
    def setup_method(self):
        self.target = api.SnapshotManager()

    def test_resume_with_snapshot(self, execute, snapshot_config):
        self.target.snapshot = m.SnapShot(routine=snapshot_config, end=FUTURE)
        self.target.resume(None)
        assert execute.call_args_list == [call(snapshot_config)]

    def test_resume_without_snapshot(self, execute):
        routine = object()
        self.target.resume(routine)
        assert execute.call_args_list == [call(routine)]

    def test_resume_with_old_snapshot(self, execute, snapshot_config):
        routine = object()
        self.target.snapshot = m.SnapShot(routine=snapshot_config, end=PAST)
        self.target.resume(routine)
        assert execute.call_args_list == [call(routine)]
        assert not self.target.snapshot


@patch("orc.api.hubitat.update_light")
class TestRouteRule:
    def setup_method(self):
        self.target = api.SnapshotManager()

    def test_snapshot_update_overwrite_set(self, update_light, snapshot_config):
        rule = m.Config(set((orc.Light.b,)), config.ON, trigger=m.Trigger.SYSTEM)

        self.target.snapshot = m.SnapShot(routine=snapshot_config, end=FUTURE)
        self.target.route_rule(rule, False)
        self.target.route_rule(rule, False)

        assert self.target.snapshot.routine.items == (
            m.Config(orc.Light.a, config.ON),
            m.Config(orc.Light.b, config.ON, trigger=m.Trigger.SYSTEM),
        )
        assert update_light.call_args_list == [call(orc.Light.b, on=True), call(orc.Light.b, on=True)]

    def test_snapshot_update_add(self, update_light, snapshot_config):
        rule = m.Config(orc.Light.c, config.ON, trigger=m.Trigger.SYSTEM)

        self.target.snapshot = m.SnapShot(routine=snapshot_config, end=FUTURE)
        self.target.route_rule(rule, False)

        assert self.target.snapshot.routine.items == (
            m.Config(orc.Light.a, config.ON),
            m.Config(orc.Light.b, config.OFF),
            rule,
        )
        assert update_light.call_args_list == [call(orc.Light.c, on=True)]

    def test_rule_ignored(self, update_light, snapshot_config):
        rule = m.Config(orc.Light.c, config.ON)

        self.target.snapshot = m.SnapShot(routine=snapshot_config, end=FUTURE)
        self.target.route_rule(rule, False)

        assert self.target.snapshot.routine.items == (
            m.Config(orc.Light.a, config.ON),
            m.Config(orc.Light.b, config.OFF),
        )
        assert update_light.call_args_list == []

    def test_rule_old_snapshot(self, update_light, snapshot_config):
        rule = m.Config(orc.Light.c, config.ON)

        self.target.snapshot = m.SnapShot(routine=snapshot_config, end=PAST)
        self.target.route_rule(rule, False)

        assert self.target.snapshot is None
        assert update_light.call_args_list == [call(orc.Light.c, on=True)]

    def test_snapshot_bypassed(self, update_light, snapshot_config):
        rule = m.Config(orc.Light.c, config.ON)

        self.target.snapshot = m.SnapShot(routine=snapshot_config, end=FUTURE)

        self.target.route_rule(rule, True)

        assert self.target.snapshot.routine.items == (
            m.Config(orc.Light.a, config.ON),
            m.Config(orc.Light.b, config.OFF),
        )
        assert update_light.call_args_list == [call(orc.Light.c, on=True)]


def test_unwrapper_function_single_rule():
    calls = []
    rule = m.Config(orc.Light.a, config.ON)

    @api.unwrap_rule_container
    def target(e):
        calls.append(e)

    target(m.Config(orc.Light.a, config.ON))

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
    rule = m.Config(orc.Light.a, config.ON)

    class Foo:
        @api.unwrap_rule_container
        def target(self, e):
            calls.append(e)

    Foo().target(m.Config(orc.Light.a, config.ON))

    assert calls == [rule]


def test_unwrapper_class_routine(snapshot_config):
    calls = []

    class Foo:
        @api.unwrap_rule_container
        def target(self, e):
            calls.append(e)

    Foo().target(snapshot_config)

    assert calls == list(snapshot_config.items)


@freeze_time(datetime(2026, 1, 5, 12, tzinfo=config.tz))
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
@freeze_time(datetime(2026, 1, 3, 12, tzinfo=config.tz))
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


@freeze_time(datetime(2026, 1, 5, 12, tzinfo=config.tz))
class TestPresence:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.target = api.SnapshotManager()
        self.ctx = type("Ctx", (), {"snapshot_manager": self.target})()

    @staticmethod
    def _routine(name, trigger):
        return m.Routine(name, time(8, 0), (m.Config(orc.Light.a, config.OFF, trigger=trigger),))

    def test_mark_and_query(self):
        assert api.present_names() == set()
        api.mark_present(["Alice"], when=api.local_now())
        assert api.present_names() == {"Alice"}

    def test_expire(self):
        api.mark_present(["Alice"], when=api.local_now() - timedelta(minutes=1))
        api.expire_presence(["Alice"])
        assert api.present_names() == set()

    def test_stale_entry_outside_12h_window(self):
        api.mark_present(["Alice"], when=datetime(2026, 1, 4, 23, 30, tzinfo=config.tz))
        assert api.present_names() == set()

    def test_run_iot_job_skips_when_presence_absent(self):
        rule = self._routine("partner-r", "Alice")
        with patch.object(self.target, "route_rule") as route:
            api.run_iot_job(m.IotJob(rule), ctx=self.ctx)
        route.assert_not_called()

    def test_run_iot_job_runs_when_presence_present(self):
        api.mark_present(["Alice"], when=api.local_now())
        rule = self._routine("partner-r", "Alice")
        with patch.object(self.target, "route_rule") as route:
            api.run_iot_job(m.IotJob(rule), ctx=self.ctx)
        route.assert_called_once_with(rule, False)

    def test_run_iot_job_runs_when_no_presence_required(self):
        rule = m.Routine("r", time(8, 0), ())
        with patch.object(self.target, "route_rule") as route:
            api.run_iot_job(m.IotJob(rule), ctx=self.ctx)
        route.assert_called_once_with(rule, False)

    def test_run_iot_job_force_bypasses_presence(self):
        rule = self._routine("partner-r", "Alice")
        with patch.object(self.target, "route_rule") as route:
            api.run_iot_job(m.IotJob(rule), ctx=self.ctx, force=True)
        route.assert_called_once_with(rule, True)

    def test_run_iot_job_system_trigger_bypasses_presence(self):
        rule = self._routine("reset-r", m.Trigger.SYSTEM)
        with patch.object(self.target, "route_rule") as route:
            api.run_iot_job(m.IotJob(rule), ctx=self.ctx)
        route.assert_called_once_with(rule, False)

    def test_run_iot_job_anyone_trigger_runs_when_someone_present(self):
        api.mark_present(["Bob"], when=api.local_now())
        rule = self._routine("anyone-r", m.Trigger.ANYONE)
        with patch.object(self.target, "route_rule") as route:
            api.run_iot_job(m.IotJob(rule), ctx=self.ctx)
        route.assert_called_once_with(rule, False)

    def test_run_iot_job_anyone_trigger_skips_when_no_one_present(self):
        rule = self._routine("anyone-r", m.Trigger.ANYONE)
        with patch.object(self.target, "route_rule") as route:
            api.run_iot_job(m.IotJob(rule), ctx=self.ctx)
        route.assert_not_called()

    def test_replay_day_skips_routines_for_absent_people(self):
        past = datetime(2026, 1, 5, 8, tzinfo=config.tz)
        partner = self._routine("partner-r", "Alice")
        with patch.object(api, "get_schedule", return_value=[(past, partner)]), patch.object(api, "execute") as execute:
            api.replay_day(api.local_now())
        squished = execute.call_args.args[0]
        assert squished.items == ()

    def test_replay_day_runs_routines_for_present_people(self):
        api.mark_present(["Alice"], when=api.local_now())
        past = datetime(2026, 1, 5, 8, tzinfo=config.tz)
        partner = self._routine("partner-r", "Alice")
        with patch.object(api, "get_schedule", return_value=[(past, partner)]), patch.object(api, "execute") as execute:
            api.replay_day(api.local_now())
        squished = execute.call_args.args[0]
        assert [c.trigger for c in squished.items] == ["Alice"]

    def test_check_presence_continues_when_one_ping_raises(self):
        with patch.object(config, "people", {"Alice": {"alice.local"}, "Bob": {"bob.local"}}):

            def ping(host):
                if host == "alice.local":
                    raise RuntimeError("dns boom")
                return True

            with patch.object(api.icmplib, "ping", side_effect=lambda host, **_: MagicMock(is_alive=ping(host))):
                api.check_presence(ctx=self.ctx)
        assert api.present_names() == {"Bob"}
