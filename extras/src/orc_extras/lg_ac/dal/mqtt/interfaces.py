from typing import Protocol

from orc_extras.lg_ac.model import ACState


class Transport(Protocol):
    """The AC state surface the plugin reads through; the Thinq adapter is the real backend."""

    def fetch_state(self, device_id: str) -> ACState: ...
    def devices(self) -> list[str]: ...
    def default_device(self) -> str | None: ...
