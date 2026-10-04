from enum import Enum

from orc_engine import model as em

from orc import model as m


class Light(Enum):
    a = 1
    b = 2


def test_devices_wrapped_command_is_hashable():
    assert hash(em.Command(m.Devices(Light.a), m.ON))
