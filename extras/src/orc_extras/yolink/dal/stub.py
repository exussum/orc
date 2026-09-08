from collections.abc import Sequence
from typing import Any

from orc import model as m
from orc_extras.yolink.dal.interfaces import ConnectionCallback, ReportCallback


def authenticate(secrets: m.Secrets, timeout: int) -> tuple[str, int]:
    return "stub-token", 10**9


def fetch_leak_states(access_token: str, device_ids: Sequence[str], timeout: int) -> dict[str, Any]:
    return {device_id: {"online": True, "state": {"state": "normal", "battery": 4}} for device_id in device_ids}


def connect(access_token: str, on_connection: ConnectionCallback, on_report: ReportCallback, timeout: int) -> "_Session":
    on_connection(True)
    return _Session()


class _Session:
    def close(self) -> None:
        pass
