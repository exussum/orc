from typing import Protocol

from orc.model import DeviceEnum
from orc_extras.lgtv.dal.sqlite import Connection


class WebOsBackend(Protocol):
    def pair(self, connection: Connection, hostname: str) -> str | None: ...
    def is_off(self, tv: DeviceEnum) -> bool: ...
    def off(self, connection: Connection, tv: DeviceEnum) -> None: ...
