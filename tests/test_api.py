from datetime import date, datetime, time
from unittest.mock import call, patch

import pytest
from freezegun import freeze_time

import orc
from orc import api, config
from orc import model as m


@pytest.fixture
def snapshot_config():
    return m.Configs(m.Config(orc.Light.a, config.ON), m.Config(orc.Light.b, config.OFF))


@patch("orc.api.execute")
class TestManagingConfig:
    def setup_method(self):
        self.target = api.ConfigManager()

    def test_resume_with_snapshot(self, execute, snapshot_config):
        snapshot = self.target.snapshot = api.SnapShot(routine=snapshot_config, end=datetime(2100, 1, 1, tzinfo=config.tz))
        self.target.resume(None)
        assert execute.call_args_list == [call(snapshot.routine)]

    def test_resume_with_without_snapshot(self, execute):
        routine = object()
        self.target.resume(routine)
        assert execute.call_args_list == [call(routine)]

    def test_resume_with_old_snapshot(self, execute, snapshot_config):
        routine = object()
        self.target.snapshot = api.SnapShot(routine=snapshot_config, end=datetime(2000, 1, 1, tzinfo=config.tz))
        self.target.resume(routine)
        assert execute.call_args_list == [call(routine)]
        assert not self.target.snapshot


@patch("orc.api.dal.set_light")
class TestRouteRule:
    def setup_method(self):
        self.target = api.ConfigManager()

    def test_snapshot_update_overwrite_set(self, set_light, snapshot_config):
        rule = m.Config(set((orc.Light.b,)), config.ON, mandatory=True)

        self.target.snapshot = api.SnapShot(routine=snapshot_config, end=datetime(2100, 1, 1, tzinfo=config.tz))
        self.target.route_rule(rule, False)
        self.target.route_rule(rule, False)

        assert self.target.snapshot.routine.items == (
            m.Config(orc.Light.a, config.ON),
            m.Config(orc.Light.b, config.ON, mandatory=True),
        )
        assert set_light.call_args_list == [call(orc.Light.b, on=True), call(orc.Light.b, on=True)]

    def test_snapshot_update_add(self, set_light, snapshot_config):
        rule = m.Config(orc.Light.c, config.ON, mandatory=True)

        self.target.snapshot = api.SnapShot(routine=snapshot_config, end=datetime(2100, 1, 1, tzinfo=config.tz))
        self.target.route_rule(rule, False)

        assert self.target.snapshot.routine.items == (
            m.Config(orc.Light.a, config.ON),
            m.Config(orc.Light.b, config.OFF),
            rule,
        )
        assert set_light.call_args_list == [call(orc.Light.c, on=True)]

    def test_rule_ignored(self, set_light, snapshot_config):
        rule = m.Config(orc.Light.c, config.ON)

        self.target.snapshot = api.SnapShot(routine=snapshot_config, end=datetime(2100, 1, 1, tzinfo=config.tz))
        self.target.route_rule(rule, False)

        assert self.target.snapshot.routine.items == (
            m.Config(orc.Light.a, config.ON),
            m.Config(orc.Light.b, config.OFF),
        )
        assert set_light.call_args_list == []

    def test_rule_old_snapshot(self, set_light, snapshot_config):
        rule = m.Config(orc.Light.c, config.ON)

        self.target.snapshot = api.SnapShot(routine=snapshot_config, end=datetime(2000, 1, 1, tzinfo=config.tz))
        self.target.route_rule(rule, False)

        assert self.target.snapshot == None
        assert set_light.call_args_list == [call(orc.Light.c, on=True)]

    def test_snapshot_bypassed(self, set_light, snapshot_config):
        rule = m.Config(orc.Light.c, config.ON)

        self.target.snapshot = api.SnapShot(routine=snapshot_config, end=datetime(2100, 1, 1, tzinfo=config.tz))

        self.target.route_rule(rule, True)

        assert self.target.snapshot.routine.items == (
            m.Config(orc.Light.a, config.ON),
            m.Config(orc.Light.b, config.OFF),
        )
        assert set_light.call_args_list == [call(orc.Light.c, on=True)]


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
    OVERRIDE = api.ThemeOverride("vacation", date(2026, 1, 1), date(2026, 1, 10))

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.target = api.ConfigManager()
        self.target.set_theme_override(*self.OVERRIDE)

    def test_no_override(self):
        self.target.theme_override = None
        assert self.target.active_override(date(2026, 1, 5)) is None

    def test_active_inside_window(self):
        assert self.target.active_override(date(2026, 1, 5)) == self.OVERRIDE

    def test_active_on_start_boundary(self):
        assert self.target.active_override(date(2026, 1, 1)) == self.OVERRIDE

    def test_active_on_end_boundary(self):
        assert self.target.active_override(date(2026, 1, 10)) == self.OVERRIDE

    def test_inactive_before_window(self):
        assert self.target.active_override(date(2025, 12, 31)) is None

    def test_inactive_after_window(self):
        assert self.target.active_override(date(2026, 1, 11)) is None


# 2026-01-03 is Saturday, 2026-01-04 is Sunday
@freeze_time(datetime(2026, 1, 3, 12, tzinfo=config.tz))
class TestGetSchedule:
    @staticmethod
    def _theme(name, *routine_names):
        return m.Theme(name, *(m.Routine(n, time(8, 0), ()) for n in routine_names))

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.target = api.ConfigManager()
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
        self.target.set_theme_override("vacation", date(2026, 1, 3), date(2026, 1, 4))
        assert self._names(api.get_schedule(self.target)) == ["vac-r", "vac-r"]

    def test_empty_override_clears_weekday_named_theme(self):
        self.themes["empty"] = self._theme("empty")
        self.target.set_theme_override("empty", date(2026, 1, 3), date(2026, 1, 4))
        assert self._names(api.get_schedule(self.target)) == []

    def test_weekday_named_theme_used_when_no_override(self):
        assert self._names(api.get_schedule(self.target)) == ["sat-r", "sun-r"]

    def test_falls_back_to_calculate_theme_when_no_weekday_match(self):
        del self.themes["saturday"]
        del self.themes["sunday"]
        assert self._names(api.get_schedule(self.target)) == ["off-r", "off-r"]

    def test_override_outside_window_does_not_apply(self):
        self.themes["vacation"] = self._theme("vacation", "vac-r")
        self.target.set_theme_override("vacation", date(2025, 12, 1), date(2025, 12, 31))
        assert self._names(api.get_schedule(self.target)) == ["sat-r", "sun-r"]
