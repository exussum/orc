"""Adapter routing shared by every broker backend: the adapter list, the listener bus,
inbound documents routed by their first topic segment to the adapter that declared
that namespace, and commands routed by device type to the adapter's ``encode``.
The ``adapter`` provider is registered at boot; plugins register theirs during setup.
"""

import json
import logging
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from orc import model as m

if TYPE_CHECKING:
    from orc.dal.interfaces import Adapter

_log = logging.getLogger(__name__)

adapters: list["Adapter"] = []
status_listeners: list[m.Listener] = []  # run on the broker's thread; keep them fast and don't block


def register(adapter: "Adapter", publish: Callable[[m.Message], None]) -> None:
    for other in adapters:
        if taken := set(other.namespaces) & set(adapter.namespaces) or set(other.device_types) & set(adapter.device_types):
            raise ValueError(f"mqtt: {', '.join(sorted(taken))} already handled by {type(other).__name__}")
    adapters.append(adapter)
    adapter.attach(publish)


def add_status_listener(fn: m.Listener) -> None:
    status_listeners.append(fn)


def snapshot() -> list[m.DeviceState]:
    return [state for adapter in adapters for state in adapter.snapshot()]


def adapter_for(device: m.DeviceEnum) -> "Adapter":
    adapter = next((c for c in adapters if device.kind in c.device_types), None)
    if adapter is None:
        raise LookupError(f"mqtt: no adapter speaks for `{device.kind}`")
    return adapter


def receive(topic: str, payload: bytes) -> None:
    namespace = topic.split("/", 1)[0]
    adapter = next((c for c in adapters if namespace in c.namespaces), None)
    if adapter is None:
        return
    try:
        statuses = adapter.decode(topic, parse(payload))
    except Exception:
        _log.exception("mqtt: message handling failed for %s", topic)
        return
    for status in statuses:
        _fire(status_listeners, topic, status)


# The adapter always sees a dict: the parsed object, or {} for an empty payload (the
# hub's command echoes and button-clearing publishes), a bare value (a setLevel
# echo), or anything that isn't JSON. The AC appends a null terminator to its JSON.
def parse(payload: bytes) -> dict[str, Any]:
    try:
        doc = json.loads(payload.rstrip(b"\x00")) if payload else {}
    except ValueError:
        return {}
    return doc if isinstance(doc, dict) else {}


def _fire(listeners: Sequence[m.Listener], topic: str, *args: Any) -> None:
    for listener in list(listeners):
        try:
            listener(*args)
        except Exception:
            _log.exception("mqtt: listener failed for %s", topic)
