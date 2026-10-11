import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, create_autospec

import pytest
from freezegun import freeze_time

sys.path.insert(0, str(Path(__file__).parents[2] / "examples" / "plugin"))

import orc
from orc import api
from orc import model as m
from orc.kernel.declarations import Declarations

FIXTURE = Path(__file__).parent / "fixture"
NOW = datetime(2024, 1, 1, 15, tzinfo=UTC)

orc.config.load(m.Secrets(), {})


def plugin_config(plugin, name):
    return {plugin.CONFIG: (FIXTURE / f"{name}.orc").read_text()}


@pytest.fixture(autouse=True)
def reset_ctx():
    api.set_ctx(m.AppContext(MagicMock(), api.runtime()))


@pytest.fixture(autouse=True)
def _device_enums(request, monkeypatch):
    if enums := getattr(request.module, "DEVICE_ENUMS", None):
        monkeypatch.setattr(orc.config, "registry", Declarations().build(enums))


@pytest.fixture
def ctx():
    mock = MagicMock()
    mock.model = m
    mock.api = create_autospec(api)
    mock.scheduler = create_autospec(m.Scheduler, instance=True)
    mock.api.device_state.side_effect = lambda target: next(
        (s for s in mock.api.device_states.return_value if str(s.device.id) == target or s.device.name == target), None
    )
    return mock


@pytest.fixture
def freezegun_clock(ctx, monkeypatch):
    monkeypatch.setattr(orc.config, "settings", orc.config.settings._replace(tz=UTC))
    with freeze_time(NOW) as frozen:
        ctx.frozen = frozen
        ctx.api.local_now = api.local_now
        ctx.config.settings.tz = UTC
        yield frozen
