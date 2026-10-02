from unittest.mock import MagicMock

import pytest
from orc_extras import yolink

import orc
from orc import model as m


def test_yolink_registers_with_core():
    assert "Leak" in orc.config.registry.devices
    assert hasattr(orc, "Leak")  # enum built from the registered device type
    assert yolink.setup in orc.config.registry.setup_hooks


def test_simulate_transition_unknown_sensor_returns_false(ctx):
    ctx.plugin_state = {yolink: yolink.plugins.states_for(())}
    assert yolink.plugins.simulate_transition(ctx, "no-such-sensor", MagicMock()) is False


@pytest.mark.parametrize(("data", "battery"), [({"state": "alert"}, None), ({"state": "alert", "battery": 4}, m.BatteryLevel.HIGH)])
def test_report_applies_with_or_without_a_battery(ctx, data, battery):
    device = MagicMock(value="leak-1", label="Kitchen")
    ctx.plugin_state = {yolink: yolink.plugins.states_for((device,))}
    on_transition = MagicMock()
    yolink.plugins._on_report(ctx, on_transition, "leak-1", data)
    on_transition.assert_any_call("Kitchen", "leak", None, "alert")
    assert ctx.plugin_state[yolink].get("leak-1").battery is battery
