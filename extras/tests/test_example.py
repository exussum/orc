from pathlib import Path
from unittest.mock import MagicMock, create_autospec

import pytest
from orc_extras import example
from orc_extras.example import model as m
from orc_extras.example import plugins

from orc import api
from orc.loader import Cast

FIXTURE = Path(__file__).parent / "fixture"


def _setup_runtime():
    ctx = MagicMock()
    ctx.api = create_autospec(api)
    ctx.config.plugin_configs = {example.CONFIG: (FIXTURE / "example.orc").read_text()}
    example.setup(ctx)
    return plugins._runtime


def test_example_config_loads():
    rt = _setup_runtime()
    assert rt.settings == m.Settings(
        foo_backend="orc_extras.example.dal.foo.stub",
        bar_backend="orc_extras.example.dal.bar.stub",
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
    assert rt.foo is Cast.module("orc_extras.example.dal.foo.stub")
    assert rt.bar is Cast.module("orc_extras.example.dal.bar.stub")


@pytest.mark.parametrize(
    "path,func",
    [
        ("orc_extras.example.dal.foo.acme", "do_foo"),
        ("orc_extras.example.dal.foo.stub", "do_foo"),
        ("orc_extras.example.dal.bar.globex", "do_bar"),
        ("orc_extras.example.dal.bar.stub", "do_bar"),
    ],
)
def test_backends_resolve(path, func):
    assert callable(getattr(Cast.module(path), func))
