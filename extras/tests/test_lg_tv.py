from enum import Enum
from unittest.mock import MagicMock, create_autospec, patch

import pytest
from orc_extras import lg_tv
from orc_extras.lg_tv import plugins
from orc_extras.lg_tv.dal import sqlite

import orc
from orc import api
from orc import model as m


@pytest.fixture
def mock_registry(monkeypatch):
    """Install a minimal registry that wires device types to dispatch handlers, for
    tests that exercise dispatch without running real registration. Each keyword is
    ``name=(enum_cls, dispatch | None)``; the enum is also attached to ``orc``."""

    def install(ctx, **dispatch_by_type):
        for name, (cls, _) in dispatch_by_type.items():
            monkeypatch.setattr(orc, name, cls, raising=False)
        registry = m.Registry(
            devices={
                name: m.DeviceType(cls=cls, icon="", controllable=False, dispatch=dispatch)
                for name, (cls, dispatch) in dispatch_by_type.items()
            },
            scripts={},
            button_labels={},
            state_providers={},
            setup_hooks=[],
        )
        monkeypatch.setattr(orc.config, "registry", registry)
        api.set_ctx(ctx)
        return registry

    return install


class TestDispatchLGTV:
    @pytest.fixture(autouse=True)
    def _lg_tv_enums(self, mock_registry):
        class LGTV(Enum):
            living_room = 1

        class WebOS(Enum):
            living_room = 1

        class BroadLink(Enum):
            living_room = 1

        self.ctx = MagicMock()
        self.ctx.api = create_autospec(api)
        mock_registry(ctx=self.ctx, LGTV=(LGTV, lg_tv._dispatch), WebOS=(WebOS, None), BroadLink=(BroadLink, None))
        self.lg_tv = LGTV.living_room
        self.webos = WebOS.living_room
        self.bl = BroadLink.living_room

    def test_off_powers_webos_off(self):
        with patch.object(plugins, "off") as webos_off:
            api.dispatch(m.Config(self.lg_tv, m.OFF), entry=None)
        webos_off.assert_called_once_with(self.ctx.api.connection, self.webos)

    def test_on_toggles_broadlink_when_tv_is_off(self):
        with patch.object(plugins, "is_off", return_value=True):
            api.dispatch(m.Config(self.lg_tv, m.ON), entry=None)
        self.ctx.api.tv_toggle.assert_called_once_with(self.bl)

    def test_on_skips_toggle_when_tv_already_on(self):
        with patch.object(plugins, "is_off", return_value=False):
            api.dispatch(m.Config(self.lg_tv, m.ON), entry=None)
        self.ctx.api.tv_toggle.assert_not_called()

    def test_device_command_routes_to_lg_tv_handler(self):
        with patch.object(plugins, "off") as webos_off:
            api.device_command("living_room", m.OFF)
        webos_off.assert_called_once_with(self.ctx.api.connection, self.webos)


def test_lg_tv_registers_with_core():
    from orc import config

    assert lg_tv.setup in config.registry.setup_hooks
    with patch.object(sqlite, "init_db") as init_db:
        ctx = MagicMock()
        ctx.api = create_autospec(api)
        ctx.config.plugin_configs = {}
        lg_tv.setup(ctx)
    init_db.assert_called_once_with(ctx.api.connection)
    assert config.registry.state_providers["TV"] is lg_tv.tv_state

    lg_tv_dev = config.registry.devices["LGTV"]
    assert lg_tv_dev.dispatch is lg_tv._dispatch
    assert lg_tv_dev.controllable
    assert lg_tv_dev.icon == "tv"
    assert config.registry.scripts["lg_tv.js"].is_file()
