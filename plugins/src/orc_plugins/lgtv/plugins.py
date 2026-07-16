from orc_plugins.lgtv import webos

from orc.plugins import PluginCtx


def pair_tv(ctx: PluginCtx, *, device: str) -> None:
    # Driven by a device-row click, which passes the TV via ?device=<name>.
    webos.pair(ctx.orc.WebOS[device].value)
