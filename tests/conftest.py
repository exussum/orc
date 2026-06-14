from enum import Enum

import pytest


def pytest_sessionstart(session):
    import orc

    class Light(Enum):
        a = 1
        b = 2
        c = 3

    class Chromecast(Enum):
        x = 1

    class TV(Enum):
        t = 1

    orc.Light, orc.Chromecast, orc.TV = Light, Chromecast, TV


@pytest.fixture(autouse=True)
def _orc_state_db(tmp_path, monkeypatch):
    from orc import config
    from orc.dal import sqlite

    monkeypatch.setattr(config, "jobs_db", f"sqlite:///{tmp_path / 'state.sqlite'}")
    sqlite.init_db()
    yield
