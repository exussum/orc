from datetime import UTC, datetime
from unittest.mock import MagicMock, create_autospec, patch

import pytest
from orc_extras import lg_tv
from orc_extras.lg_tv import plugins
from orc_extras.lg_tv.dal import sqlite

import orc
from orc import api
from orc import model as m
from orc.kernel import engine


@pytest.fixture
def mock_registry(monkeypatch):
    """Install a minimal registry that wires device types to dispatch handlers, for
    tests that exercise dispatch without running real registration. Each keyword is
    ``name=(enum_cls, dispatch | None)``; the enum is also attached to ``orc``."""

    def install(ctx, **dispatch_by_type):
        for name, (cls, _) in dispatch_by_type.items():
            monkeypatch.setattr(orc, name, cls, raising=False)
        devices = m.DeviceNamespace(**{name: cls for name, (cls, _) in dispatch_by_type.items()})
        registry = m.Registry(
            devices=devices,
            device_icons={},
            controllable_devices=frozenset(),
            dispatch_handlers={name: dispatch for name, (_, dispatch) in dispatch_by_type.items() if dispatch is not None},
            scripts={},
            button_labels={},
            state_providers={},
            setup_hooks=[],
        )
        monkeypatch.setattr(orc.config, "registry", registry)
        ctx.config.registry = registry
        ctx.config.devices = devices
        api.set_ctx(ctx)
        return registry

    return install


class TestDispatchLGTV:
    @pytest.fixture(autouse=True)
    def _lg_tv_enums(self, mock_registry):
        class LGTV(m.DeviceEnum):
            living_room = 1

        class WebOS(m.DeviceEnum):
            living_room = 1

        class BroadLink(m.DeviceEnum):
            living_room = 1

        self.ctx = MagicMock()
        self.ctx.api = create_autospec(api)
        self.ctx.engine = engine.Runtime([])
        mock_registry(ctx=self.ctx, LGTV=(LGTV, lg_tv._dispatch), WebOS=(WebOS, None), BroadLink=(BroadLink, None))
        self.lg_tv = LGTV.living_room
        self.webos = WebOS.living_room
        self.bl = BroadLink.living_room
        self.entry = m.LogEntry(datetime.now(UTC), m.LogSource.MANUAL, "test")

    def test_off_powers_webos_off(self):
        with patch.object(plugins, "off") as webos_off:
            api.dispatch((engine.Command(m.Devices(self.lg_tv), m.OFF),), entry=self.entry)
        webos_off.assert_called_once_with(self.ctx, self.webos)

    def test_on_toggles_broadlink_when_tv_is_off(self):
        with patch.object(plugins, "is_off", return_value=True):
            api.dispatch((engine.Command(m.Devices(self.lg_tv), m.ON),), entry=self.entry)
        self.ctx.api.tv_toggle.assert_called_once_with(self.bl)

    def test_on_skips_toggle_when_tv_already_on(self):
        with patch.object(plugins, "is_off", return_value=False):
            api.dispatch((engine.Command(m.Devices(self.lg_tv), m.ON),), entry=self.entry)
        self.ctx.api.tv_toggle.assert_not_called()

    def test_device_command_routes_to_lg_tv_handler(self):
        with patch.object(plugins, "off") as webos_off:
            api.device_command("living_room", m.OFF, self.entry)
        webos_off.assert_called_once_with(self.ctx, self.webos)


def test_lg_tv_registers_with_core():
    from orc import config

    assert lg_tv.setup in config.registry.setup_hooks
    with patch.object(sqlite, "init_db") as init_db:
        ctx = MagicMock()
        ctx.api = create_autospec(api)
        ctx.config.plugin_configs = {}
        lg_tv.setup(ctx)
    init_db.assert_called_once_with(ctx.api.connection)
    name, provider = ctx.api.add_state_provider.call_args.args
    assert name == "TV"
    assert provider.func is lg_tv.tv_state

    assert config.registry.dispatch_handlers["LGTV"] is lg_tv._dispatch
    assert "LGTV" in config.registry.controllable_devices
    assert config.registry.device_icons["LGTV"] == "tv"
    assert config.registry.scripts["lg_tv.js"].is_file()
