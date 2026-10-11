from collections.abc import Callable, Sequence
from typing import Any

from orc import model as m

REQUIRED_SECRETS: dict[str, Callable[[str], Any]] = {}

namespaces: tuple[str, ...] = ()
device_types: tuple[str, ...] = ()


def attach(publish: Callable[[m.Message], None]) -> None:
    pass


def decode(topic: str, doc: dict[str, Any]) -> tuple[m.Status, ...]:
    return ()


def encode(device: m.DeviceEnum, command: Any) -> tuple[m.Message, ...]:
    return ()


def snapshot() -> tuple[m.DeviceState, ...]:
    return ()


def start() -> None:
    pass


def discover(messages: Sequence[m.Message]) -> dict[str, tuple[str, frozenset[m.Capability]]]:
    return {}
