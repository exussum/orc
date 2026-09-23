import sys
from pathlib import Path
from unittest.mock import MagicMock, create_autospec

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "examples" / "plugin"))

import orc
from orc import api
from orc import model as m

orc.config.load(m.Secrets(), {})


@pytest.fixture(autouse=True)
def _reset_ctx():
    api.set_ctx(m.AppContext(MagicMock(), MagicMock()))


@pytest.fixture
def ctx():
    mock = MagicMock()
    mock.model = m
    mock.api = create_autospec(api)
    return mock
