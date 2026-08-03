import sys
from enum import Enum
from types import ModuleType

import pytest

from orc import declarations


@pytest.fixture
def fake_plugin():
    """A throwaway plugin package exposing a declare(declarations) hook."""
    mod = ModuleType("fake_plugin_pkg")

    def declare(declarations):
        declarations.declare(device_types=["FakeDevice"], button_labels={"Fake": "Run {device}"})

    mod.declare = declare
    sys.modules["fake_plugin_pkg"] = mod
    try:
        yield
    finally:
        sys.modules.pop("fake_plugin_pkg", None)


def test_collect_declarations_invokes_declare_hook(fake_plugin):
    builder = declarations.collect_declarations(["fake_plugin_pkg.plugins.some_fn"], {})
    assert "FakeDevice" in builder.device_types
    assert builder.build({}).button_labels["Fake"] == "Run {device}"


def test_collect_declarations_dedupes_package(fake_plugin):
    # A package listed by several plugins registers once, so FakeDevice appears once.
    builder = declarations.collect_declarations(["fake_plugin_pkg.plugins.a", "fake_plugin_pkg.plugins.b"], {})
    assert builder.device_types.count("FakeDevice") == 1


def test_collect_declarations_skips_core_and_missing_declare():
    # orc.* paths are core plugins (no package declare hook); must be skipped, so the
    # returned builder carries only the core defaults.
    builder = declarations.collect_declarations(["orc.plugins.light_test"], {})
    assert builder.device_types == declarations.Declarations().device_types


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


def test_build_carries_reset_excluded_flag():
    class Excluded(Enum):
        x = 1

    class Kept(Enum):
        x = 1

    builder = declarations.Declarations()
    builder.declare(reset_excluded=["Excluded"])
    reg = builder.build({"Excluded": Excluded, "Kept": Kept})
    assert reg.devices["Excluded"].reset_excluded
    assert not reg.devices["Kept"].reset_excluded


def test_declare_wires_every_piece():
    class Acme(Enum):
        x = 1

    def hook():
        pass

    def handler(w, rule, stream):
        pass

    builder = declarations.Declarations()
    builder.declare(
        device_types=["Acme"],
        dispatch={"Acme": handler},
        setup=[hook],
    )
    assert "Acme" in builder.device_types
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
