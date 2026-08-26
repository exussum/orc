from typing import TYPE_CHECKING, cast

import orc
import orc_extras.lgtv
from orc.loader import resolve_backend
from orc.model import AppContext, DeviceEnum, LogEntry
from orc_extras.lgtv.dal.interfaces import WebOsBackend
from orc_extras.lgtv.dal.sqlite import Connection


def _backend() -> WebOsBackend:
    return cast(WebOsBackend, resolve_backend(orc.config.plugin_for(orc_extras.lgtv).backend))


def pair(connection: Connection, hostname: str) -> str | None:
    return _backend().pair(connection, hostname)


def is_off(tv: DeviceEnum) -> bool:
    return _backend().is_off(tv)


def off(connection: Connection, tv: DeviceEnum) -> None:
    _backend().off(connection, tv)


def pair_tv(ctx: AppContext, device: str, *, entry: LogEntry) -> None:
    pair(ctx.api.connection, ctx.orc.WebOS[device].value)


if TYPE_CHECKING:
    from orc_extras.lgtv.dal.tv import stub, webos

    _real: WebOsBackend = webos
    _stub: WebOsBackend = stub
