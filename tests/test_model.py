from orc import model as m
from orc.kernel import engine
from orc.model import DeviceEnum


class Light(DeviceEnum):
    a = 1
    b = 2
    c = 3


class Chromecast(DeviceEnum):
    x = 1


Light._sort = 0
Chromecast._sort = 1


def _cmd(what, value):
    return engine.Command(m.Devices(what), value)


def test_squish_dim_then_off():
    cfg = (_cmd(Light.a, 10), _cmd(Light.a, m.ON), _cmd(Light.a, 20), _cmd(Light.a, m.ON), _cmd(Light.a, m.OFF))
    assert m._squish(list(cfg)) == (_cmd(Light.a, 20), _cmd(Light.a, m.OFF))


def test_squish_just_off():
    cfg = [_cmd(Light.a, m.ON), _cmd(Light.a, m.OFF)]
    assert m._squish(cfg) == (_cmd(Light.a, m.OFF),)


def test_squish_dim_on():
    cfg = [_cmd(Light.a, 20), _cmd(Light.a, m.ON)]
    assert m._squish(cfg) == (_cmd(Light.a, 20), _cmd(Light.a, m.ON))


def test_squish_0_on():
    cfg = [_cmd(Light.a, 0), _cmd(Light.a, m.ON)]
    assert m._squish(cfg) == (_cmd(Light.a, 0), _cmd(Light.a, m.ON))


def test_squish_just_on():
    cfg = [_cmd(Light.a, m.OFF), _cmd(Light.a, m.ON)]
    assert m._squish(cfg) == (_cmd(Light.a, m.ON),)


def test_theme_squish_everything_off_start():
    commands = (_cmd(Light, m.OFF), _cmd(Light.a, m.ON))
    assert m.squish(commands) == (_cmd(Light.a, m.ON), _cmd(Light.b, m.OFF), _cmd(Light.c, m.OFF))


def test_theme_squish_double_on():
    commands = (_cmd(Light, m.ON), _cmd(Light.a, m.ON))
    assert m.squish(commands) == (_cmd(Light.a, m.ON), _cmd(Light.b, m.ON), _cmd(Light.c, m.ON))


def test_theme_squish_dim_then_off():
    commands = (_cmd(Light, m.OFF), _cmd(Light.a, 10), _cmd(Light, m.OFF))
    assert m.squish(commands) == (_cmd(Light.a, 10), _cmd(Light.a, m.OFF), _cmd(Light.b, m.OFF), _cmd(Light.c, m.OFF))


def test_squish_stop_then_volume():
    commands = (_cmd(Chromecast, "stop"), _cmd(Chromecast, "stop"), _cmd(Chromecast.x, 10))
    assert m.squish(commands) == (_cmd(Chromecast.x, "stop"), _cmd(Chromecast.x, 10))


def test_op_cmp_dim():
    assert m._op_cmp(_cmd(Light.a, 50)) == (0, -1)


def test_op_cmp_on():
    assert m._op_cmp(_cmd(Light.a, m.ON)) == (0, 0)


def test_op_cmp_off():
    assert m._op_cmp(_cmd(Light.a, m.OFF)) == (0, 1)


def test_op_cmp_sorts_dim_before_on_before_off():
    commands = [_cmd(Light.a, m.OFF), _cmd(Light.b, m.ON), _cmd(Light.c, 50)]
    assert sorted(commands, key=m._op_cmp) == [_cmd(Light.c, 50), _cmd(Light.b, m.ON), _cmd(Light.a, m.OFF)]


def test_op_cmp_sorts_by_type_sort():
    commands = [_cmd(Chromecast.x, m.ON), _cmd(Light.a, m.ON)]
    assert sorted(commands, key=m._op_cmp) == [_cmd(Light.a, m.ON), _cmd(Chromecast.x, m.ON)]
