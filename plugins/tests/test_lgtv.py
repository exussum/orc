from enum import Enum
from unittest.mock import patch

import pytest
from orc_plugins import lgtv
from orc_plugins.lgtv import webos

import orc
from orc import api
from orc import model as m
from orc.dal import broadlink


@pytest.fixture
def mock_registry(monkeypatch):
    """Install a minimal registry that wires device types to dispatch handlers, for
    tests that exercise dispatch without running real registration. Each keyword is
    ``name=(enum_cls, dispatch | None)``; the enum is also attached to ``orc``."""

    def install(**dispatch_by_type):
        for name, (cls, _) in dispatch_by_type.items():
            monkeypatch.setattr(orc, name, cls, raising=False)
        registry = m.Registry(
            devices={
                name: m.DeviceType(cls=cls, icon="", controllable=False, reset_excluded=False, dispatch=dispatch)
                for name, (cls, dispatch) in dispatch_by_type.items()
            },
            click_hooks={},
            button_labels={},
            state_providers={},
            startup_hooks=[],
        )
        monkeypatch.setattr(orc.config, "registry", registry)
        return registry

    return install


class TestDispatchLGTV:
    @pytest.fixture(autouse=True)
    def _lgtv_enums(self, mock_registry):
        class LGTV(Enum):
            living_room = 1

        class WebOS(Enum):
            living_room = 1

        class BroadLink(Enum):
            living_room = 1

        # LGTV routes to lgtv._dispatch; the backing WebOS/BroadLink types carry no handler.
        mock_registry(LGTV=(LGTV, lgtv._dispatch), WebOS=(WebOS, None), BroadLink=(BroadLink, None))
        self.lgtv = LGTV.living_room
        self.webos = WebOS.living_room
        self.bl = BroadLink.living_room

    def test_off_powers_webos_off(self):
        with patch.object(webos, "off") as webos_off:
            api.dispatch(m.Config(self.lgtv, m.OFF))
        webos_off.assert_called_once_with(self.webos)

    def test_on_toggles_broadlink_when_tv_is_off(self):
        with patch.object(webos, "is_off", return_value=True), patch.object(broadlink, "tv_toggle") as tv_toggle:
            api.dispatch(m.Config(self.lgtv, m.ON))
        tv_toggle.assert_called_once_with(self.bl, lgtv._BROADLINK_CODES)

    def test_on_skips_toggle_when_tv_already_on(self):
        with patch.object(webos, "is_off", return_value=False), patch.object(broadlink, "tv_toggle") as tv_toggle:
            api.dispatch(m.Config(self.lgtv, m.ON))
        tv_toggle.assert_not_called()

    def test_device_command_routes_to_lgtv_handler(self):
        with patch.object(webos, "off") as webos_off:
            api.device_command("living_room", m.OFF)
        webos_off.assert_called_once_with(self.webos)


def test_lgtv_registers_with_core():
    from orc import config

    assert "TV" in config.registry.state_providers
    assert webos.init_db in config.registry.startup_hooks

    lgtv_dev = config.registry.devices["LGTV"]
    assert lgtv_dev.dispatch is lgtv._dispatch
    assert lgtv_dev.controllable
    assert lgtv_dev.icon == "tv"
    assert config.registry.devices["WebOS"].reset_excluded
    assert "Pair LG TV" in config.registry.click_hooks
    assert config.registry.devices["Light"].icon == "light-bulb"  # default
