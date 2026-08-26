from enum import Enum
from unittest.mock import create_autospec

import pytest

from orc import model as m
from orc.model import DeviceEnum


@pytest.fixture(autouse=True)
def core_registry(monkeypatch):
    """Install a registry built from lightweight test enums for the duration of each
    core test; monkeypatch restores what was there before at teardown.

    ``orc.config`` is a process-wide singleton whose ``registry`` is populated with the
    real, plugin-inclusive device set at import. The core suite needs a small fixed set
    of enums instead — but must not leave that override in place: when the plugin suite
    runs in the same process it asserts against the real registry. So we save/restore
    rather than clobber. Autouse in this conftest ⇒ scoped to tests/."""
    import orc
    from orc import api, declarations

    class Light(DeviceEnum):
        a = (1, frozenset([m.Capability.change_level]))
        b = (2, frozenset())
        c = (3, frozenset())

    class Chromecast(Enum):
        x = 1

    # Register core dispatch into a fresh builder, then build the registry from the test
    # enums — mirroring the app's post-api reload so config.registry.dispatch is populated.
    builder = declarations.Declarations()
    api.declare_core(builder)
    monkeypatch.setattr(orc, "Light", Light, raising=False)
    monkeypatch.setattr(orc, "Chromecast", Chromecast, raising=False)
    monkeypatch.setattr(orc.config, "registry", builder.build({"Light": Light, "Chromecast": Chromecast}))
    yield


@pytest.fixture(autouse=True)
def reset_stubs():
    from orc.dal.chromecast import stub as chromecast_stub
    from orc.dal.mqtt import stub as mqtt_stub

    mqtt_stub.reset()
    chromecast_stub.reset()
    yield


@pytest.fixture(autouse=True)
def mute_speakers(monkeypatch):
    from orc import api

    monkeypatch.setattr(api, "play_text", create_autospec(api.play_text))
    monkeypatch.setattr(api, "play_alert", create_autospec(api.play_alert))
    yield


@pytest.fixture(autouse=True)
def orc_state_db(tmp_path, monkeypatch):
    from orc import config
    from orc.dal import sqlite

    monkeypatch.setattr(config, "settings", config.settings._replace(jobs_db=f"sqlite:///{tmp_path / 'state.sqlite'}"))
    sqlite.init_db()
    yield
