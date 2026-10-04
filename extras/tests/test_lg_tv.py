from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from orc_engine import engine
from orc_extras.lg_tv import plugins

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
    def _lg_tv_enums(self, mock_registry, ctx):
        class LGTV(m.DeviceEnum):
            living_room = 1

        class WebOS(m.DeviceEnum):
            living_room = 1

        class BroadLink(m.DeviceEnum):
            living_room = 1

        self.ctx = ctx
        self.ctx.engine = engine.Runtime(lambda _subject: datetime.now(UTC))
        mock_registry(ctx=self.ctx, LGTV=(LGTV, plugins._dispatch), WebOS=(WebOS, None), BroadLink=(BroadLink, None))
        self.lg_tv = LGTV.living_room
        self.webos = WebOS.living_room
        self.bl = BroadLink.living_room
        self.entry = m.LogEntry(datetime.now(UTC), m.LogSource.MANUAL, "test", m.Manual("test"))

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
