from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from orc import model as m
from orc.dal import broker, warn_stub

if TYPE_CHECKING:
    from orc.dal.interfaces import Adapter

REQUIRED_SECRETS: dict[str, Callable[[str], Any]] = {}

warn_stub("broker")


def register(adapter: "Adapter") -> None:
    broker.register(adapter, _drop)


def start() -> None:
    for adapter in broker.adapters:
        adapter.start()


def command(device: m.DeviceEnum, value: Any) -> None:
    broker.adapter_for(device).encode(device, value)


def discover(adapter: "Adapter", secrets: m.Secrets, timeout: float = 3.0) -> dict[str, tuple[str, frozenset[m.Capability]]]:
    return adapter.discover(())


def _drop(message: m.Message) -> None:
    pass
