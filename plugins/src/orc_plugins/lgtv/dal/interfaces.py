from typing import Protocol

from orc_plugins.lgtv.dal.sqlite import Connection

from orc.model import DeviceEnum


class WebOsBackend(Protocol):
    def pair(self, connection: Connection, hostname: str) -> str | None: ...
    def is_off(self, tv: DeviceEnum) -> bool: ...
    def off(self, connection: Connection, tv: DeviceEnum) -> None: ...
