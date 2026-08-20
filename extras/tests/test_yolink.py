from orc_extras import yolink

import orc


def test_yolink_registers_with_core():
    assert "Leak" in orc.config.registry.devices
    assert hasattr(orc, "Leak")  # enum built from the registered device type
    assert yolink.setup in orc.config.registry.setup_hooks
    assert orc.config.registry.state_providers["Leak Sensors"] is yolink.leak_state


def test_simulate_transition_unknown_sensor_returns_false():
    assert yolink.plugins.simulate_transition("no-such-sensor") is False
