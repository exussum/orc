from pathlib import Path

import pytest
from command_cfg import array, group, scalar
from orc_plugins import example
from orc_plugins.example.model import Settings, Widget, Zone

from orc.loader import load_plugin_config
from orc.model import column_to_value

FIXTURE = Path(__file__).parent / "fixture"


def _load():
    return load_plugin_config(
        example.CONFIG,
        {example.CONFIG: (FIXTURE / "example.orc").read_text()},
        example.GRAMMAR,
        serializers={"setting": scalar(Settings), "widget": array(Widget), "zone": group(Zone)},
    )


def test_example_config_loads():
    config = _load()
    assert config.setting == Settings(
        foo_backend="orc_plugins.example.dal.foo.stub",
        bar_backend="orc_plugins.example.dal.bar.stub",
        cron="0 6 * * *",
        window_hours=6,
        foo_secret="FOO_KEY",
        bar_secret="BAR_KEY",
        http_timeout=120,
    )
    assert config.widget == [Widget("Alpha", 10), Widget("Beta", 20)]
    assert config.zone["north"] == [Zone("Home", "123 Main St, Springfield"), Zone("Office", "500 Market St, Metropolis")]
    assert config.zone["south"] == [Zone("Villa", "9 Beach Rd, Seaside")]


@pytest.mark.parametrize(
    "path,func",
    [
        ("orc_plugins.example.dal.foo.acme", "do_foo"),
        ("orc_plugins.example.dal.foo.stub", "do_foo"),
        ("orc_plugins.example.dal.bar.globex", "do_bar"),
        ("orc_plugins.example.dal.bar.stub", "do_bar"),
    ],
)
def test_backends_resolve(path, func):
    assert callable(getattr(column_to_value("module", path), func))
