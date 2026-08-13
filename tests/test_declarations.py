from enum import Enum
from types import ModuleType

import pytest

from orc import declarations


@pytest.fixture
def fake_plugin():
    """A throwaway plugin module exposing a declare(declarations) hook."""
    mod = ModuleType("fake_plugin_pkg")
    mod.calls = []

    def declare(declarations):
        mod.calls.append(1)
        declarations.declare(button_labels={"Fake": "Run {device}"})

    mod.declare = declare
    return mod


def test_collect_declarations_invokes_declare_hook(fake_plugin):
    builder = declarations.collect_declarations([fake_plugin])
    assert builder.build({}).button_labels["Fake"] == "Run {device}"


def test_collect_declarations_dedupes_module(fake_plugin):
    declarations.collect_declarations([fake_plugin, fake_plugin])
    assert fake_plugin.calls == [1]


def test_collect_declarations_skips_core_modules():
    from orc import plugins as core_plugins

    builder = declarations.collect_declarations([core_plugins])
    assert builder.button_labels == {}


def test_build_carries_dispatch_and_missing():
    class FakeType(Enum):
        x = 1

    def handler(w, rule, stream):
        pass

    builder = declarations.Declarations()
    builder.declare_dispatch("FakeType", handler)
    reg = builder.build({"FakeType": FakeType, "NoDispatch": FakeType})
    assert reg.devices["FakeType"].dispatch is handler
    assert reg.devices["NoDispatch"].dispatch is None


def test_declare_wires_every_piece():
    class Acme(Enum):
        x = 1

    def hook():
        pass

    def handler(w, rule, stream):
        pass

    builder = declarations.Declarations()
    builder.declare(
        dispatch={"Acme": handler},
        setup=[hook],
    )
    reg = builder.build({"Acme": Acme})
    assert reg.devices["Acme"].dispatch is handler
    assert reg.state_providers == {}
    assert hook in reg.setup_hooks


def test_setup_hooks_run_and_dedupe():
    calls = []

    def hook():
        calls.append(1)

    builder = declarations.Declarations()
    builder.declare(setup=[hook])
    builder.declare(setup=[hook])  # identity dedupe -> registered once
    assert builder.setup_hooks.count(hook) == 1
    for h in builder.build({}).setup_hooks:
        h()
    assert calls == [1]
