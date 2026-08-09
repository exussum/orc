from enum import Enum

from orc import model as m


class Light(Enum):
    a = 1
    b = 2
    c = 3


class Chromecast(Enum):
    x = 1


def test_squish_dim_then_off():
    cfg = (
        m.Config(Light.a, 10),
        m.Config(Light.a, m.ON),
        m.Config(Light.a, 20),
        m.Config(Light.a, m.ON),
        m.Config(Light.a, m.OFF),
    )
    assert m._squish(cfg) == (
        m.Config(Light.a, 20),
        m.Config(Light.a, m.OFF),
    )


def test_squish_just_off():
    cfg = (m.Config(Light.a, m.ON), m.Config(Light.a, m.OFF))
    assert m._squish(cfg) == (m.Config(Light.a, m.OFF),)


def test_squish_dim_on():
    cfg = (m.Config(Light.a, 20), m.Config(Light.a, m.ON))
    assert m._squish(cfg) == (
        m.Config(Light.a, 20),
        m.Config(Light.a, m.ON),
    )


def test_squish_0_on():
    cfg = (m.Config(Light.a, 0), m.Config(Light.a, m.ON))
    assert m._squish(cfg) == (
        m.Config(Light.a, 0),
        m.Config(Light.a, m.ON),
    )


def test_squish_just_on():
    cfg = (m.Config(Light.a, m.OFF), m.Config(Light.a, m.ON))
    assert m._squish(cfg) == (m.Config(Light.a, m.ON),)


def test_theme_squish_everything_off_start():
    routine = m.Configs(m.Config(Light, m.OFF), m.Config(Light.a, m.ON))
    assert m.squish_configs(routine) == m.Configs(
        m.Config(Light.a, m.ON, trigger=None),
        m.Config(Light.b, m.OFF, trigger=None),
        m.Config(Light.c, m.OFF, trigger=None),
    )


def test_theme_squish_double_on():
    routine = m.Configs(m.Config(Light, m.ON), m.Config(Light.a, m.ON))
    assert m.squish_configs(routine) == m.Configs(
        m.Config(Light.a, m.ON, trigger=None),
        m.Config(Light.b, m.ON, trigger=None),
        m.Config(Light.c, m.ON, trigger=None),
    )


def test_theme_squish_dim_then_off():
    routine = m.Configs(
        m.Config(Light, m.OFF),
        m.Config(Light.a, 10),
        m.Config(Light, m.OFF),
    )
    assert m.squish_configs(routine) == m.Configs(
        m.Config(Light.a, 10, trigger=None),
        m.Config(Light.a, m.OFF, trigger=None),
        m.Config(Light.b, m.OFF, trigger=None),
        m.Config(Light.c, m.OFF, trigger=None),
    )


def test_squish_configs_stop_then_volume():
    routine = m.Configs(
        m.Config(Chromecast, "stop"),
        m.Config(Chromecast, "stop"),
        m.Config(Chromecast.x, 10),
    )
    assert m.squish_configs(routine) == m.Configs(
        m.Config(Chromecast.x, "stop", trigger=None),
        m.Config(Chromecast.x, 10, trigger=None),
    )


def test_op_cmp_dim():
    assert m._op_cmp(m.Config(Light.a, 50)) == (0, -1)


def test_op_cmp_on():
    assert m._op_cmp(m.Config(Light.a, m.ON)) == (0, 0)


def test_op_cmp_off():
    assert m._op_cmp(m.Config(Light.a, m.OFF)) == (0, 1)


def test_op_cmp_sorts_dim_before_on_before_off():
    configs = [m.Config(Light.a, m.OFF), m.Config(Light.b, m.ON), m.Config(Light.c, 50)]
    assert sorted(configs, key=m._op_cmp) == [m.Config(Light.c, 50), m.Config(Light.b, m.ON), m.Config(Light.a, m.OFF)]


def test_op_cmp_sorts_by_class_name():
    configs = [m.Config(Chromecast.x, m.ON), m.Config(Light.a, m.ON)]
    assert sorted(configs, key=m._op_cmp) == [m.Config(Light.a, m.ON), m.Config(Chromecast.x, m.ON)]
