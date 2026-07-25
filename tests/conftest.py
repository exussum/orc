from enum import Enum

import pytest

from orc import model as m
from orc.model import DeviceEnum


@pytest.fixture(autouse=True)
def _core_registry():
    """Install a registry built from lightweight test enums for the duration of each
    core test, then restore what was there before.

    ``orc.config`` is a process-wide singleton whose ``registry`` is populated with the
    real, plugin-inclusive device set at import. The core suite needs a small fixed set
    of enums instead — but must not leave that override in place: when the plugin suite
    runs in the same process it asserts against the real registry. So we save/restore
    rather than clobber. Autouse in this conftest ⇒ scoped to tests/."""
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

    saved_attrs = {name: getattr(orc, name, None) for name in ("Light", "Chromecast", "TV", "device_enums")}
    saved_registry = orc.config.registry

    # Register core dispatch into a fresh builder, then build the registry from the test
    # enums — mirroring the app's post-api reload so config.registry.dispatch is populated.
    builder = device_registry.RegistryBuilder()
    api.register_core(builder)
    orc.Light, orc.Chromecast, orc.TV = Light, Chromecast, TV
    orc.device_enums = [Light, Chromecast, TV]
    orc.config.registry = builder.build({"Light": Light, "Chromecast": Chromecast, "TV": TV})
    try:
        yield
    finally:
        for name, val in saved_attrs.items():
            setattr(orc, name, val)
        orc.config.registry = saved_registry


@pytest.fixture(autouse=True)
def _orc_state_db(tmp_path, monkeypatch):
    from orc import config
    from orc.dal import sqlite

    monkeypatch.setattr(config, "jobs_db", f"sqlite:///{tmp_path / 'state.sqlite'}")
    sqlite.init_db()
    yield
