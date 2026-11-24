from enum import Enum

from orc_engine import model as em

from orc import model as m


class Light(Enum):
    a = 1
    b = 2


def test_devices_wrapped_command_is_a_dict_key_by_value():
    seen = {em.Command(m.Devices(Light.a), m.ON): "a on"}
    assert seen[em.Command(m.Devices(Light.a), m.ON)] == "a on"
    assert em.Command(m.Devices(Light.b), m.ON) not in seen
    assert em.Command(m.Devices(Light.a), m.OFF) not in seen
