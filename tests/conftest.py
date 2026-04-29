from enum import Enum

import pytest


def pytest_sessionstart(session):
    from orc import config

    class Light(Enum):
        a = 1
        b = 2
        c = 3

    class Sound(Enum):
        x = 1

    config.Light, config.Sound = Light, Sound
    return Light, Sound
