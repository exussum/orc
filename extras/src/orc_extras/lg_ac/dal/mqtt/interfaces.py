from typing import Protocol

from orc.model import AcState


class Transport(Protocol):
    """The AC state surface the plugin reads through; the Thinq adapter is the real backend."""

    def fetch_state(self, device_id: str) -> AcState: ...
    def devices(self) -> list[str]: ...
    def default_device(self) -> str | None: ...
