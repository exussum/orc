from orc_plugins import yolink

import orc


def test_yolink_registers_with_core():
    # importing orc runs config load -> orc_plugins.register -> yolink.register
    assert "Leak" in orc.config.registry.devices
    assert hasattr(orc, "Leak")  # enum built from the registered device type
    assert yolink.setup in orc.config.registry.setup_hooks
    # providers register at startup: setup() wires Leak Sensors through the ctx
    from unittest.mock import MagicMock, patch

    ctx = MagicMock()
    with patch.object(yolink.plugins, "start"), patch.object(yolink.plugins, "set_transition_callback"):
        yolink.setup(ctx)
    ctx.api.add_state_provider.assert_called_once_with("Leak Sensors", yolink.leak_state)


def test_leak_state_rows_have_name_key():
    for row in yolink.leak_state():
        assert "name" in row


def test_simulate_transition_unknown_sensor_returns_false():
    assert yolink.plugins.simulate_transition("no-such-sensor") is False
