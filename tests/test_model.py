import pytest
from orc_engine import model as em

from orc import model as m
from orc.model import DeviceEnum


class Light(DeviceEnum):
    a = 1
    b = 2
    c = 3


class Chromecast(DeviceEnum):
    x = 1


Light._sort = 0
Chromecast._sort = 1


def test_squish_dim_then_off():
    cfg = (_cmd(Light.a, 10), _cmd(Light.a, m.ON), _cmd(Light.a, 20), _cmd(Light.a, m.ON), _cmd(Light.a, m.OFF))
    assert m._squish(list(cfg)) == (_cmd(Light.a, 20), _cmd(Light.a, m.OFF))


def test_squish_just_off():
    cfg = [_cmd(Light.a, m.ON), _cmd(Light.a, m.OFF)]
    assert m._squish(cfg) == (_cmd(Light.a, m.OFF),)


def test_squish_dim_on():
    cfg = [_cmd(Light.a, 20), _cmd(Light.a, m.ON)]
    assert m._squish(cfg) == (_cmd(Light.a, 20), _cmd(Light.a, m.ON))


def test_squish_just_on():
    cfg = [_cmd(Light.a, m.OFF), _cmd(Light.a, m.ON)]
    assert m._squish(cfg) == (_cmd(Light.a, m.ON),)


def test_squish_expands_everything_off_start():
    commands = (_cmd(Light, m.OFF), _cmd(Light.a, m.ON))
    assert m.squish(commands) == (_cmd(Light.a, m.ON), _cmd(Light.b, m.OFF), _cmd(Light.c, m.OFF))


def test_squish_expands_double_on():
    commands = (_cmd(Light, m.ON), _cmd(Light.a, m.ON))
    assert m.squish(commands) == (_cmd(Light.a, m.ON), _cmd(Light.b, m.ON), _cmd(Light.c, m.ON))


def test_squish_expands_dim_then_off():
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


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (m.AcState(power=m.OFF), m.AcState(power=m.OFF, mode="cool", fan_mode="low", temperature=72), True),
        (m.AcState(power=m.ON, mode="cool"), m.AcState(power=m.ON, mode="cool", fan_mode="low", temperature=72), True),
        (m.AcState(power=m.ON, mode="cool"), m.AcState(power=m.ON, mode="fan_only", fan_mode="low", temperature=72), False),
        (m.AcState(power=m.ON, mode="cool", current_temperature=70), m.AcState(power=m.ON, mode="cool", current_temperature=75), True),
        (m.AcState(), m.AcState(), False),
        (m.AcState(mode="cool"), m.AcState(power=m.ON, mode="cool"), False),
    ],
)
def test_ac_state_eq(a, b, expected):
    assert (a == b) is expected


def test_secrets_update_in_place_names_changes():
    secrets = m.Secrets(mqtt_user="a", other={"X": "1", "Y": "2"})
    other = secrets.other
    assert secrets.update(m.Secrets(mqtt_user="b", other={"X": "1", "Z": "3"})) == {"mqtt_user", "Y", "Z"}
    assert secrets.mqtt_user == "b"
    assert secrets.other is other
    assert secrets.other == {"X": "1", "Z": "3"}


def _cmd(what, value):
    return em.Command(m.Devices(what), value)
