from enum import Enum

from orc import model as m
from orc.kernel import engine


class Light(Enum):
    a = 1
    b = 2


def test_devices_wrapped_command_is_hashable():
    assert hash(engine.Command(m.Devices(Light.a), m.ON))
