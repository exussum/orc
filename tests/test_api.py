from datetime import datetime
from enum import Enum
from unittest.mock import Mock, call, patch

import pytest

from orc import api, config
from orc import model as m


class Light(Enum):
    a = 1
    b = 2
    c = 3


@pytest.fixture
def snapshot_config():
    return m.AdHocRoutineConfig(
        items=(m.LightSubConfig(what=Light.a, state="on"), m.LightSubConfig(what=Light.b, state="off"))
    )


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
        rule = m.LightConfig(what=set((Light.b,)), state="on", when="9:00", name="b", mandatory=True)

        target = api.ConfigManager()
        target.snapshot = api.SnapShot(routine=snapshot_config, end=None)
        target.route_rule(rule, False)
        target.route_rule(rule, False)

        assert target.snapshot.routine.items == (
            m.LightSubConfig(what=Light.a, state="on"),
            m.LightConfig(what=Light.b, state="on", when="9:00", name="b", mandatory=True),
        )
        assert set_light.call_args_list == [call(Light.b, on=True), call(Light.b, on=True)]

    def test_snapshot_update_add(self, set_light, snapshot_config):
        rule = m.LightConfig(what=Light.c, state="on", when="9:00", name="b", mandatory=True)

        target = api.ConfigManager()
        target.snapshot = api.SnapShot(routine=snapshot_config, end=None)
        target.route_rule(rule, False)

        assert target.snapshot.routine.items == (
            m.LightSubConfig(what=Light.a, state="on"),
            m.LightSubConfig(what=Light.b, state="off"),
            rule,
        )
        assert set_light.call_args_list == [call(Light.c, on=True)]

    def test_rule_ignored(self, set_light, snapshot_config):
        rule = m.LightConfig(what=Light.c, state="on", when="9:00", name="b")

        target = api.ConfigManager()
        target.snapshot = api.SnapShot(routine=snapshot_config, end=datetime(2100, 1, 1, tzinfo=config.TZ))
        target.route_rule(rule, False)

        assert target.snapshot.routine.items == (
            m.LightSubConfig(what=Light.a, state="on"),
            m.LightSubConfig(what=Light.b, state="off"),
        )
        assert set_light.call_args_list == []

    def test_rule_old_snapshot(self, set_light, snapshot_config):
        rule = m.LightConfig(what=Light.c, state="on", when="9:00", name="b")

        target = api.ConfigManager()
        target.snapshot = api.SnapShot(routine=snapshot_config, end=datetime(2000, 1, 1, tzinfo=config.TZ))
        target.route_rule(rule, False)

        assert target.snapshot == None
        assert set_light.call_args_list == [call(Light.c, on=True)]

    def test_snapshot_bypassed(self, set_light, snapshot_config):
        rule = m.LightConfig(what=Light.c, state="on", when="9:00", name="b")

        target = api.ConfigManager()
        target.snapshot = api.SnapShot(routine=snapshot_config, end=datetime(2100, 1, 1, tzinfo=config.TZ))

        target.route_rule(rule, True)

        assert target.snapshot.routine.items == (
            m.LightSubConfig(what=Light.a, state="on"),
            m.LightSubConfig(what=Light.b, state="off"),
        )
        assert set_light.call_args_list == [call(Light.c, on=True)]


def test_unwrapper_function_single_rule():
    calls = []
    rule = m.LightSubConfig(what=Light.a, state="on")

    @api.unwrap_rule_container
    def target(e):
        calls.append(e)

    target(m.LightSubConfig(what=Light.a, state="on"))

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
    rule = m.LightSubConfig(what=Light.a, state="on")

    class Foo:
        @api.unwrap_rule_container
        def target(self, e):
            calls.append(e)

    Foo().target(m.LightSubConfig(what=Light.a, state="on"))

    assert calls == [rule]


def test_unwrapper_class_routine(snapshot_config):
    calls = []

    class Foo:
        @api.unwrap_rule_container
        def target(self, e):
            calls.append(e)

    Foo().target(snapshot_config)

    assert calls == list(snapshot_config.items)


def test_squish_dim_then_off():
    config = (
        m.LightSubConfig(what=Light.a, state=10),
        m.LightSubConfig(what=Light.a, state="on"),
        m.LightSubConfig(what=Light.a, state=20),
        m.LightSubConfig(what=Light.a, state="on"),
        m.LightSubConfig(what=Light.a, state="off"),
    )
    assert api.squish(config) == (
        m.LightSubConfig(what=Light.a, state=20),
        m.LightSubConfig(what=Light.a, state="off"),
    )


def test_squish_just_off():
    config = (m.LightSubConfig(what=Light.a, state="on"), m.LightSubConfig(what=Light.a, state="off"))
    assert api.squish(config) == (m.LightSubConfig(what=Light.a, state="off"),)


def test_squish_dim_on():
    config = (m.LightSubConfig(what=Light.a, state=20), m.LightSubConfig(what=Light.a, state="on"))
    assert api.squish(config) == (m.LightSubConfig(what=Light.a, state=20), m.LightSubConfig(what=Light.a, state="on"))


def test_squish_0_on():
    config = (m.LightSubConfig(what=Light.a, state=0), m.LightSubConfig(what=Light.a, state="on"))
    assert api.squish(config) == (m.LightSubConfig(what=Light.a, state=0), m.LightSubConfig(what=Light.a, state="on"))


def test_squish_just_on():
    config = (m.LightSubConfig(what=Light.a, state="off"), m.LightSubConfig(what=Light.a, state="on"))
    assert api.squish(config) == (m.LightSubConfig(what=Light.a, state="on"),)


def test_theme_squish_everything_off_start():
    routine = m.AdHocRoutineConfig(
        items=(m.LightSubConfig(what=Light, state="off"), m.LightSubConfig(what=Light.a, state="on"))
    )
    assert api.squish_routines(routine) == m.AdHocRoutineConfig(
        items=(
            m.LightSubConfig(what=Light.a, state="on", mandatory=False),
            m.LightSubConfig(what=Light.b, state="off", mandatory=False),
            m.LightSubConfig(what=Light.c, state="off", mandatory=False),
        )
    )


def test_theme_squish_double_on():
    routine = m.AdHocRoutineConfig(
        items=(m.LightSubConfig(what=Light, state="on"), m.LightSubConfig(what=Light.a, state="on"))
    )
    assert api.squish_routines(routine) == m.AdHocRoutineConfig(
        items=(
            m.LightSubConfig(what=Light.a, state="on", mandatory=False),
            m.LightSubConfig(what=Light.b, state="on", mandatory=False),
            m.LightSubConfig(what=Light.c, state="on", mandatory=False),
        )
    )


def test_theme_squish_dim_then_off():
    routine = m.AdHocRoutineConfig(
        items=(
            m.LightSubConfig(what=Light, state="off"),
            m.LightSubConfig(what=Light.a, state=10),
            m.LightSubConfig(what=Light, state="off"),
        )
    )
    assert api.squish_routines(routine) == m.AdHocRoutineConfig(
        items=(
            m.LightSubConfig(what=Light.a, state=10, mandatory=False),
            m.LightSubConfig(what=Light.a, state="off", mandatory=False),
            m.LightSubConfig(what=Light.b, state="off", mandatory=False),
            m.LightSubConfig(what=Light.c, state="off", mandatory=False),
        )
    )
