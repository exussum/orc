import pytest

from orc import model as m
from orc.model import DeviceEnum


def pytest_sessionstart(session):
    from enum import Enum

    import orc
    from orc import api, device_registry

    class Light(DeviceEnum):
        a = (1, frozenset([m.Capability.change_level]))
        b = (2, frozenset())
        c = (3, frozenset())

    class Chromecast(Enum):
        x = 1

    class TV(Enum):
        t = 1

    orc.Light, orc.Chromecast, orc.TV = Light, Chromecast, TV
    orc.device_enums = [Light, Chromecast, TV]
    # Register core dispatch into a fresh builder, then build the registry from the test
    # enums — mirroring the app's post-api reload so config.registry.dispatch is populated.
    builder = device_registry.RegistryBuilder()
    api.register_core(builder)
    orc.config.registry = builder.build({"Light": Light, "Chromecast": Chromecast, "TV": TV})


@pytest.fixture(autouse=True)
def _orc_state_db(tmp_path, monkeypatch):
    from orc import config
    from orc.dal import sqlite

    monkeypatch.setattr(config, "jobs_db", f"sqlite:///{tmp_path / 'state.sqlite'}")
    sqlite.init_db()
    yield
