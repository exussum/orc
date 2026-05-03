from datetime import datetime
from unittest.mock import call, patch

import pytest

from orc import api, config
from orc import model as m


@pytest.fixture
def snapshot_config():
    return m.Configs(m.Config(config.Light.a, config.ON), m.Config(config.Light.b, config.OFF))


@patch("orc.api.execute")
class TestManagingConfig:
    def test_resume_with_snapshot(self, execute, snapshot_config):
        target = api.ConfigManager()
        snapshot = target.snapshot = api.SnapShot(routine=snapshot_config, end=datetime(2100, 1, 1, tzinfo=config.TZ))
        target.resume(None)
        assert execute.call_args_list == [call(snapshot.routine)]

    def test_resume_with_without_snapshot(self, execute):
        routine = object()
        target = api.ConfigManager()
        target.resume(routine)
        assert execute.call_args_list == [call(routine)]

    def test_resume_with_old_snapshot(self, execute, snapshot_config):
        routine = object()
        target = api.ConfigManager()
        target.snapshot = api.SnapShot(routine=snapshot_config, end=datetime(2000, 1, 1, tzinfo=config.TZ))
        target.resume(routine)
        assert execute.call_args_list == [call(routine)]
        assert not target.snapshot


@patch("orc.api.dal.set_light")
class TestRouteRule:
    def test_snapshot_update_overwrite_set(self, set_light, snapshot_config):
        rule = m.Config(set((config.Light.b,)), config.ON, mandatory=True)

        target = api.ConfigManager()
        target.snapshot = api.SnapShot(routine=snapshot_config, end=None)
        target.route_rule(rule, False)
        target.route_rule(rule, False)

        assert target.snapshot.routine.items == (
            m.Config(config.Light.a, config.ON),
            m.Config(config.Light.b, config.ON, mandatory=True),
        )
        assert set_light.call_args_list == [call(config.Light.b, on=True), call(config.Light.b, on=True)]

    def test_snapshot_update_add(self, set_light, snapshot_config):
        rule = m.Config(config.Light.c, config.ON, mandatory=True)

        target = api.ConfigManager()
        target.snapshot = api.SnapShot(routine=snapshot_config, end=None)
        target.route_rule(rule, False)

        assert target.snapshot.routine.items == (
            m.Config(config.Light.a, config.ON),
            m.Config(config.Light.b, config.OFF),
            rule,
        )
        assert set_light.call_args_list == [call(config.Light.c, on=True)]

    def test_rule_ignored(self, set_light, snapshot_config):
        rule = m.Config(config.Light.c, config.ON)

        target = api.ConfigManager()
        target.snapshot = api.SnapShot(routine=snapshot_config, end=datetime(2100, 1, 1, tzinfo=config.TZ))
        target.route_rule(rule, False)

        assert target.snapshot.routine.items == (
            m.Config(config.Light.a, config.ON),
            m.Config(config.Light.b, config.OFF),
        )
        assert set_light.call_args_list == []

    def test_rule_old_snapshot(self, set_light, snapshot_config):
        rule = m.Config(config.Light.c, config.ON)

        target = api.ConfigManager()
        target.snapshot = api.SnapShot(routine=snapshot_config, end=datetime(2000, 1, 1, tzinfo=config.TZ))
        target.route_rule(rule, False)

        assert target.snapshot == None
        assert set_light.call_args_list == [call(config.Light.c, on=True)]

    def test_snapshot_bypassed(self, set_light, snapshot_config):
        rule = m.Config(config.Light.c, config.ON)

        target = api.ConfigManager()
        target.snapshot = api.SnapShot(routine=snapshot_config, end=datetime(2100, 1, 1, tzinfo=config.TZ))

        target.route_rule(rule, True)

        assert target.snapshot.routine.items == (
            m.Config(config.Light.a, config.ON),
            m.Config(config.Light.b, config.OFF),
        )
        assert set_light.call_args_list == [call(config.Light.c, on=True)]


def test_unwrapper_function_single_rule():
    calls = []
    rule = m.Config(config.Light.a, config.ON)

    @api.unwrap_rule_container
    def target(e):
        calls.append(e)

    target(m.Config(config.Light.a, config.ON))

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
    rule = m.Config(config.Light.a, config.ON)

    class Foo:
        @api.unwrap_rule_container
        def target(self, e):
            calls.append(e)

    Foo().target(m.Config(config.Light.a, config.ON))

    assert calls == [rule]


def test_unwrapper_class_routine(snapshot_config):
    calls = []

    class Foo:
        @api.unwrap_rule_container
        def target(self, e):
            calls.append(e)

    Foo().target(snapshot_config)

    assert calls == list(snapshot_config.items)
