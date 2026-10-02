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
    api.set_ctx(m.AppContext(MagicMock(), api.runtime()))


@pytest.fixture
def ctx():
    mock = MagicMock()
    mock.model = m
    mock.api = create_autospec(api)
    mock.scheduler = create_autospec(m.Scheduler, instance=True)
    mock.api.device_state.side_effect = lambda target: next(
        (s for s in mock.api.device_states.return_value if str(s.id) == target or s.name == target), None
    )
    return mock
