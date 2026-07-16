import sys
from enum import Enum
from types import ModuleType

import pytest

from orc import device_registry


@pytest.fixture
def fake_plugin():
    """A throwaway plugin package exposing a register(core) hook."""
    mod = ModuleType("fake_plugin_pkg")

    def register(core):
        core.register_plugin(device_types=["FakeDevice"], state_providers={"Fake": lambda: [{"name": "f", "ok": True}]})

    mod.register = register
    sys.modules["fake_plugin_pkg"] = mod
    try:
        yield
    finally:
        sys.modules.pop("fake_plugin_pkg", None)


def test_run_registration_invokes_register_hook(fake_plugin):
    builder = device_registry.run_registration(["fake_plugin_pkg.plugins.some_fn"])
    assert "FakeDevice" in builder.device_types
    assert builder.build({}).state_providers["Fake"]() == [{"name": "f", "ok": True}]


def test_run_registration_dedupes_package(fake_plugin):
    # A package listed by several plugins registers once, so FakeDevice appears once.
    builder = device_registry.run_registration(["fake_plugin_pkg.plugins.a", "fake_plugin_pkg.plugins.b"])
    assert builder.device_types.count("FakeDevice") == 1


def test_run_registration_skips_core_and_missing_register():
    # orc.* paths are core plugins (no package register hook); must be skipped, so the
    # returned builder carries only the core defaults.
    builder = device_registry.run_registration(["orc.plugins.light_test"])
    assert builder.device_types == device_registry.RegistryBuilder().device_types


def test_build_carries_dispatch_and_missing():
    class FakeType(Enum):
        x = 1

    def handler(w, rule, stream):
        pass

    builder = device_registry.RegistryBuilder()
    builder.register_dispatch("FakeType", handler)
    reg = builder.build({"FakeType": FakeType, "NoDispatch": FakeType})
    assert reg.devices["FakeType"].dispatch is handler
    assert reg.devices["NoDispatch"].dispatch is None


def test_build_carries_reset_excluded_flag():
    class Excluded(Enum):
        x = 1

    class Kept(Enum):
        x = 1

    builder = device_registry.RegistryBuilder()
    builder.register_plugin(reset_excluded=["Excluded"])
    reg = builder.build({"Excluded": Excluded, "Kept": Kept})
    assert reg.devices["Excluded"].reset_excluded
    assert not reg.devices["Kept"].reset_excluded


def test_register_plugin_wires_every_piece():
    class Acme(Enum):
        x = 1

    def hook():
        pass

    def handler(w, rule, stream):
        pass

    builder = device_registry.RegistryBuilder()
    builder.register_plugin(
        device_types=["Acme"],
        dispatch={"Acme": handler},
        state_providers={"Acme": lambda: [{"name": "a"}]},
        startup=[hook],
    )
    assert "Acme" in builder.device_types
    reg = builder.build({"Acme": Acme})
    assert reg.devices["Acme"].dispatch is handler
    assert reg.state_providers["Acme"]() == [{"name": "a"}]
    assert hook in reg.startup_hooks


def test_startup_hooks_run_and_dedupe():
    calls = []

    def hook():
        calls.append(1)

    builder = device_registry.RegistryBuilder()
    builder.register_plugin(startup=[hook])
    builder.register_plugin(startup=[hook])  # identity dedupe -> registered once
    assert builder.startup_hooks.count(hook) == 1
    for h in builder.build({}).startup_hooks:
        h()
    assert calls == [1]
