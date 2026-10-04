from typing import TYPE_CHECKING, Any

from orc_engine import cast
from orc_engine import model as em

import orc_extras.lg_tv
from orc.kernel.loader import resolve_backend
from orc.model import OFF, ON, AppContext, DeviceEnum, DeviceStatus, LogEntry
from orc_extras.lg_tv.dal.interfaces import WebOsBackend


def backend(ctx: AppContext) -> WebOsBackend:
    return cast.instance(resolve_backend(ctx.config.plugin_for(orc_extras.lg_tv).backend), WebOsBackend)


def pair(ctx: AppContext, hostname: str) -> str | None:
    return backend(ctx).pair(ctx.api.connection, hostname)


def is_off(ctx: AppContext, tv: DeviceEnum) -> bool:
    return backend(ctx).is_off(tv)


def off(ctx: AppContext, tv: DeviceEnum) -> None:
    backend(ctx).off(ctx.api.connection, tv)


def _dispatch(ctx: AppContext, w: DeviceEnum, command: "em.Command[Any]", stream: dict[Any, tuple[str, str]]) -> None:
    webos_device, bl_device = ctx.config.devices.WebOS[w.name], ctx.config.devices.BroadLink[w.name]
    if command.value == OFF:
        off(ctx, webos_device)
    elif command.value == ON:
        if is_off(ctx, webos_device):
            ctx.api.tv_toggle(bl_device)
    else:
        raise Exception(f"LGTV only supports on and off, got: {command.value!r}")


def tv_state(ctx: AppContext, backend: WebOsBackend) -> list[DeviceStatus]:
    # ``action`` makes each row a clickable runner -> /api/run/Pair LG TV?device=<name>.
    return [
        DeviceStatus(
            name=w.name,
            label=w.label,
            action="Pair LG TV",
            details={"state": "off" if backend.is_off(ctx.config.devices.WebOS[w.name]) else "on"},
        )
        for w in ctx.config.devices.LGTV
    ]


def pair_tv(ctx: AppContext, device: str, *, entry: LogEntry) -> None:
    pair(ctx, ctx.config.devices.WebOS[device].value)


if TYPE_CHECKING:
    from orc_extras.lg_tv.dal import stub, webos

    _real: WebOsBackend = webos
    _stub: WebOsBackend = stub
