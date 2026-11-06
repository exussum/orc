"""The real broker connection with publishing disabled: subscribe, receive and discover, drop every command."""

import logging
import sys
from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Any

from orc import model as m
from orc.dal import broker
from orc.dal.broker import paho

if TYPE_CHECKING:
    from orc.dal.interfaces import Adapter

_log = logging.getLogger(__name__)

REQUIRED_SECRETS: dict[str, Callable[[str], Any]] = paho.REQUIRED_SECRETS

print("warning: broker provider is read-only — subscribed to the real broker, every publish is dropped", file=sys.stderr)


def register(adapter: "Adapter") -> None:
    broker.register(adapter, partial(_drop, adapter))


def start() -> None:
    paho.start()


def command(device: m.DeviceEnum, value: Any) -> None:
    adapter = broker.adapter_for(device)
    for message in adapter.encode(device, value):
        _drop(adapter, message)


def discover(adapter: "Adapter", secrets: m.Secrets, timeout: float = 3.0) -> dict[str, tuple[str, frozenset[m.Capability]]]:
    return paho.discover(adapter, secrets, timeout)


def _drop(adapter: "Adapter", message: m.Message) -> None:
    _log.info("mqtt read-only: dropped %s publish on %s", type(adapter).__name__, message.topic)
