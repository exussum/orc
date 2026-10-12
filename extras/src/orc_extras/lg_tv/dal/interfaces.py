from typing import Protocol, runtime_checkable

from orc.model import DeviceEnum


@runtime_checkable
class WebOsBackend(Protocol):
    def pair(self, hostname: str) -> str | None: ...
    def is_off(self, tv: DeviceEnum) -> bool: ...
    def off(self, tv: DeviceEnum) -> None: ...
