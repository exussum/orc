from pathlib import Path

import example
import pytest
from example import model as m
from orc_engine import cast

from orc.kernel.declarations import Declarations

FIXTURE = Path(__file__).parent / "fixture"


def _setup_runtime(ctx):
    ctx.plugin_state = {}
    ctx.config.plugin_configs = {example.CONFIG: (FIXTURE / "example.orc").read_text()}
    example.setup(ctx)
    return ctx.plugin_state[example]


def test_example_declares_its_configured_keys():
    builder = Declarations(plugin_configs={example.CONFIG: (FIXTURE / "example.orc").read_text()})
    example.declare(builder)
    assert builder.secrets == {"FOO_KEY": cast.nonblank, "BAR_KEY": cast.nonblank}


def test_example_config_loads(ctx):
    rt = _setup_runtime(ctx)
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
    assert rt.foo is cast.module("example.dal.foo.stub")
    assert rt.bar is cast.module("example.dal.bar.stub")


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
    assert callable(getattr(cast.module(path), func))
