from typing import TYPE_CHECKING

import orc_extras.lg_tv
from orc.kernel import cast
from orc.kernel.loader import resolve_backend
from orc.model import AppContext, DeviceEnum, LogEntry
from orc_extras.lg_tv.dal.interfaces import WebOsBackend


def backend(ctx: AppContext) -> WebOsBackend:
    return cast.instance(resolve_backend(ctx.config.plugin_for(orc_extras.lg_tv).backend), WebOsBackend)


def pair(ctx: AppContext, hostname: str) -> str | None:
    return backend(ctx).pair(ctx.api.connection, hostname)


def is_off(ctx: AppContext, tv: DeviceEnum) -> bool:
    return backend(ctx).is_off(tv)


def off(ctx: AppContext, tv: DeviceEnum) -> None:
    backend(ctx).off(ctx.api.connection, tv)


def pair_tv(ctx: AppContext, device: str, *, entry: LogEntry) -> None:
    pair(ctx, ctx.config.devices.WebOS[device].value)


if TYPE_CHECKING:
    from orc_extras.lg_tv.dal import stub, webos

    _real: WebOsBackend = webos
    _stub: WebOsBackend = stub
