from orc_plugins import yolink

import orc


def test_yolink_registers_with_core():
    # importing orc runs config load -> orc_plugins.register -> yolink.register
    assert "Leak" in orc.config.registry.devices
    assert hasattr(orc, "Leak")  # enum built from the registered device type
    assert "Leak Sensors" in orc.config.registry.state_providers
    assert yolink.start in orc.config.registry.startup_hooks


def test_leak_state_rows_have_name_key():
    for row in yolink.leak_state():
        assert "name" in row


def test_simulate_transition_unknown_sensor_returns_false():
    assert yolink.dal.simulate_transition("no-such-sensor") is False
