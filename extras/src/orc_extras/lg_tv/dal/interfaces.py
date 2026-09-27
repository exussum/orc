from typing import Protocol, runtime_checkable

from orc.model import DeviceEnum
from orc_extras.lg_tv.dal.sqlite import Connection


@runtime_checkable
class WebOsBackend(Protocol):
    def pair(self, connection: Connection, hostname: str) -> str | None: ...
    def is_off(self, tv: DeviceEnum) -> bool: ...
    def off(self, connection: Connection, tv: DeviceEnum) -> None: ...
