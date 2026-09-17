from pathlib import Path
from unittest.mock import MagicMock, create_autospec

import example
import pytest
from example import model as m

from orc import api
from orc.kernel.loader import Cast

FIXTURE = Path(__file__).parent / "fixture"


def _setup_runtime():
    ctx = MagicMock()
    ctx.api = create_autospec(api)
    ctx.plugin_state = {}
    ctx.config.plugin_configs = {example.CONFIG: (FIXTURE / "example.orc").read_text()}
    example.setup(ctx)
    return ctx.plugin_state[example]


def test_example_config_loads():
    rt = _setup_runtime()
    assert rt.settings == m.Settings(
        foo_backend="example.dal.foo.stub",
        bar_backend="example.dal.bar.stub",
        cron="0 6 * * *",
        window_hours=6,
        foo_secret="FOO_KEY",
        bar_secret="BAR_KEY",
        http_timeout=120,
    )
    assert rt.widgets == [m.Widget("Alpha", 10), m.Widget("Beta", 20)]
    assert rt.zones == [
        m.Zone("Home", "123 Main St, Springfield"),
        m.Zone("Office", "500 Market St, Metropolis"),
        m.Zone("Villa", "9 Beach Rd, Seaside"),
    ]
    assert rt.foo is Cast.module("example.dal.foo.stub")
    assert rt.bar is Cast.module("example.dal.bar.stub")


@pytest.mark.parametrize(
    "path,func",
    [
        ("example.dal.foo.acme", "do_foo"),
        ("example.dal.foo.stub", "do_foo"),
        ("example.dal.bar.globex", "do_bar"),
        ("example.dal.bar.stub", "do_bar"),
    ],
)
def test_backends_resolve(path, func):
    assert callable(getattr(Cast.module(path), func))
