from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol, cast

import orc_plugins.lgtv

import orc
from orc.loader import resolve_backend
from orc.model import AppContext, DeviceEnum

type Connection = Callable[[], Any]


class WebOsBackend(Protocol):
    def init_db(self, connection: Connection) -> None: ...
    def pair(self, connection: Connection, hostname: str) -> str | None: ...
    def is_off(self, tv: DeviceEnum) -> bool: ...
    def off(self, connection: Connection, tv: DeviceEnum) -> None: ...


def _backend() -> WebOsBackend:
    return cast(WebOsBackend, resolve_backend(orc.config.plugin_for(orc_plugins.lgtv).backend))


def init_db(connection: Connection) -> None:
    _backend().init_db(connection)


def pair(connection: Connection, hostname: str) -> str | None:
    return _backend().pair(connection, hostname)


def is_off(tv: DeviceEnum) -> bool:
    return _backend().is_off(tv)


def off(connection: Connection, tv: DeviceEnum) -> None:
    _backend().off(connection, tv)


def pair_tv(ctx: AppContext, device: str) -> None:
    pair(ctx.api.connection, ctx.orc.WebOS[device].value)


if TYPE_CHECKING:
    from orc.lgtv.dal import stub, webos

    _real: WebOsBackend = webos
    _stub: WebOsBackend = stub
