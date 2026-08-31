import os
from unittest.mock import patch

import pytest

os.environ["ORC_CONFIG_DIR"] = "tests"


@pytest.fixture(autouse=True)
def no_sleep():
    with patch("time.sleep"):
        yield
