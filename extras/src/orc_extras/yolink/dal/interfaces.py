from collections.abc import Callable, Sequence
from typing import Any, Protocol

from orc import model as m

# on_connection receives raw connect/disconnect events; flap suppression is the caller's job
type ConnectionCallback = Callable[[bool], None]
# on_report receives (device_id, report data)
type ReportCallback = Callable[[str, dict[str, Any]], None]


class Session(Protocol):
    def close(self) -> None: ...


class CloudBackend(Protocol):
    def authenticate(self, secrets: m.Secrets, timeout: int) -> tuple[str, int]: ...
    def fetch_leak_states(self, access_token: str, device_ids: Sequence[str], timeout: int) -> dict[str, Any]: ...
    def connect(self, access_token: str, on_connection: ConnectionCallback, on_report: ReportCallback, timeout: int) -> Session: ...
