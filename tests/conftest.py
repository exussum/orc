from enum import Enum

import pytest


def pytest_sessionstart(session):
    import orc

    class Light(Enum):
        a = 1
        b = 2
        c = 3

    class Sound(Enum):
        x = 1

    orc.Light, orc.Sound = Light, Sound
    return Light, Sound


@pytest.fixture(autouse=True)
def _orc_state_db(tmp_path, monkeypatch):
    from orc import config, dal

    monkeypatch.setattr(config, "jobs_db", f"sqlite:///{tmp_path / 'state.sqlite'}")
    dal.init_db()
    yield
