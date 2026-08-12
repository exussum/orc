from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

from orc_plugins.lgtv import stub

from orc.loader import load_plugin_config
from orc.model import AppContext, DeviceEnum

type Connection = Callable[[], Any]

CONFIG = "orc_plugins/lgtv"
GRAMMAR = "backend <name> <module>"


class WebosBackend(Protocol):
    def init_db(self, connection: Connection) -> None: ...
    def pair(self, connection: Connection, hostname: str) -> str | None: ...
    def is_off(self, tv: DeviceEnum) -> bool: ...
    def off(self, connection: Connection, tv: DeviceEnum) -> None: ...


_backend: WebosBackend = stub


def _select(**row: Any) -> Any:
    return next(iter(row.values()))


def configure(plugin_configs: dict[str, str]) -> None:
    global _backend
    try:
        cfg = load_plugin_config(CONFIG, plugin_configs, GRAMMAR, serializers={"backend": _select}, scalars=("backend",))
    except FileNotFoundError:
        _backend = stub
    else:
        _backend = cfg.backend


def init_db(connection: Connection) -> None:
    _backend.init_db(connection)


def pair(connection: Connection, hostname: str) -> str | None:
    return _backend.pair(connection, hostname)


def is_off(tv: DeviceEnum) -> bool:
    return _backend.is_off(tv)


def off(connection: Connection, tv: DeviceEnum) -> None:
    _backend.off(connection, tv)


def pair_tv(ctx: AppContext, *, device: str) -> None:
    pair(ctx.api.connection, ctx.orc.WebOS[device].value)


if TYPE_CHECKING:
    from orc_plugins.lgtv import webos

    _real: WebosBackend = webos
    _stub: WebosBackend = stub
