from unittest.mock import MagicMock

from orc_extras import yolink

import orc


def test_yolink_registers_with_core():
    assert "Leak" in orc.config.registry.devices
    assert hasattr(orc, "Leak")  # enum built from the registered device type
    assert yolink.setup in orc.config.registry.setup_hooks


def test_simulate_transition_unknown_sensor_returns_false():
    ctx = MagicMock()
    ctx.plugin_state = {yolink: yolink.plugins.states_for(())}
    assert yolink.plugins.simulate_transition(ctx, "no-such-sensor", MagicMock()) is False
