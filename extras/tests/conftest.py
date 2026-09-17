import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "examples" / "plugin"))

import orc
from orc import model as m

orc.config.load(m.Secrets(), {})


@pytest.fixture(autouse=True)
def _reset_ctx():
    from unittest.mock import MagicMock

    from orc import api
    from orc import model as m

    api.set_ctx(m.AppContext(MagicMock(), MagicMock()))
