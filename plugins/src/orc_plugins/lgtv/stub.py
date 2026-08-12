from collections.abc import Callable
from typing import Any

from orc.model import DeviceEnum

type Connection = Callable[[], Any]


def init_db(connection: Connection) -> None:
    pass


def pair(connection: Connection, hostname: str) -> str | None:
    return None


def is_off(tv: DeviceEnum) -> bool:
    return True


def off(connection: Connection, tv: DeviceEnum) -> None:
    pass
