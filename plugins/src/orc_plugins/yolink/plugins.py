from orc_plugins.yolink import dal

from orc.plugins import PluginCtx


def test_sensor(ctx: PluginCtx, *, device: str) -> None:
    # Driven by a device-row click, which passes the sensor via ?device=<name>.
    dal.simulate_transition(device)
