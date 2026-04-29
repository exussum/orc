from enum import Enum

from orc import config
from orc import model as m


class Light(Enum):
    a = 1
    b = 2
    c = 3


class Sound(Enum):
    x = 1


config.Light = Light


def test_squish_dim_then_off():
    cfg = (
        m.Config(Light.a, 10),
        m.Config(Light.a, config.ON),
        m.Config(Light.a, 20),
        m.Config(Light.a, config.ON),
        m.Config(Light.a, config.OFF),
    )
    assert m.squish(cfg) == (
        m.Config(Light.a, 20),
        m.Config(Light.a, config.OFF),
    )


def test_squish_just_off():
    cfg = (m.Config(Light.a, config.ON), m.Config(Light.a, config.OFF))
    assert m.squish(cfg) == (m.Config(Light.a, config.OFF),)


def test_squish_dim_on():
    cfg = (m.Config(Light.a, 20), m.Config(Light.a, config.ON))
    assert m.squish(cfg) == (
        m.Config(Light.a, 20),
        m.Config(Light.a, config.ON),
    )


def test_squish_0_on():
    cfg = (m.Config(Light.a, 0), m.Config(Light.a, config.ON))
    assert m.squish(cfg) == (
        m.Config(Light.a, 0),
        m.Config(Light.a, config.ON),
    )


def test_squish_just_on():
    cfg = (m.Config(Light.a, config.OFF), m.Config(Light.a, config.ON))
    assert m.squish(cfg) == (m.Config(Light.a, config.ON),)


def test_theme_squish_everything_off_start():
    routine = m.Configs(m.Config(Light, config.OFF), m.Config(Light.a, config.ON))
    assert m.squish_configs(routine) == m.Configs(
        m.Config(Light.a, config.ON, mandatory=False),
        m.Config(Light.b, config.OFF, mandatory=False),
        m.Config(Light.c, config.OFF, mandatory=False),
    )


def test_theme_squish_double_on():
    routine = m.Configs(m.Config(Light, config.ON), m.Config(Light.a, config.ON))
    assert m.squish_configs(routine) == m.Configs(
        m.Config(Light.a, config.ON, mandatory=False),
        m.Config(Light.b, config.ON, mandatory=False),
        m.Config(Light.c, config.ON, mandatory=False),
    )


def test_theme_squish_dim_then_off():
    routine = m.Configs(
        m.Config(Light, config.OFF),
        m.Config(Light.a, 10),
        m.Config(Light, config.OFF),
    )
    assert m.squish_configs(routine) == m.Configs(
        m.Config(Light.a, 10, mandatory=False),
        m.Config(Light.a, config.OFF, mandatory=False),
        m.Config(Light.b, config.OFF, mandatory=False),
        m.Config(Light.c, config.OFF, mandatory=False),
    )


def test_squish_configs_stop_then_volume():
    routine = m.Configs(
        m.Config(Sound, "stop"),
        m.Config(Sound.x, 50),
    )
    assert m.squish_configs(routine) == m.Configs(
        m.Config(Sound.x, "stop", mandatory=False),
        m.Config(Sound.x, 50, mandatory=False),
    )


def test_op_cmp_dim():
    assert m._op_cmp(m.Config(Light.a, 50)) == ("Light", -1)


def test_op_cmp_on():
    assert m._op_cmp(m.Config(Light.a, config.ON)) == ("Light", 0)


def test_op_cmp_off():
    assert m._op_cmp(m.Config(Light.a, config.OFF)) == ("Light", 1)


def test_op_cmp_sorts_dim_before_on_before_off():
    configs = [m.Config(Light.a, config.OFF), m.Config(Light.b, config.ON), m.Config(Light.c, 50)]
    assert sorted(configs, key=m._op_cmp) == [m.Config(Light.c, 50), m.Config(Light.b, config.ON), m.Config(Light.a, config.OFF)]


def test_op_cmp_sorts_by_class_name():
    configs = [m.Config(Sound.x, config.ON), m.Config(Light.a, config.ON)]
    assert sorted(configs, key=m._op_cmp) == [m.Config(Light.a, config.ON), m.Config(Sound.x, config.ON)]
