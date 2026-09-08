import os
from unittest.mock import patch

import pytest

os.environ["ORC_CONFIG_DIR"] = "tests"

import orc  # noqa: E402
from orc import model as m  # noqa: E402

orc.config.load(m.Secrets(), {})


@pytest.fixture(autouse=True)
def no_sleep():
    with patch("time.sleep"):
        yield
