"""ThinQ2 (clip) adapter.

Rides orc's broker connection as the adapter for the ``clip`` and ``lime``
namespaces. Merges the latest raw TLV values per device from
``clip/message/devices/<did>`` (decoded on read), answers provisioning on
``clip/provisioning/devices/<did>``, and encodes commands downstream on
``lime/devices/<did>``.

The clip transport (the upstream/downstream topics and the JSON ``packet``
envelope) is from anszom's rethink: https://github.com/anszom/rethink.
"""

from __future__ import annotations

import base64
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict
from typing import Any

from orc import model as om
from orc.collections import LockedDict
from orc_extras.lg_ac import api
from orc_extras.lg_ac import model as m

_log = logging.getLogger(__name__)

SOURCE = "lg_ac"

# The device publishes upstream to clip/message and clip/provisioning, but it
# subscribes to a firmware-baked-in downstream topic (lime/devices/<did>) and
# ignores the topics we advertise. All server->device traffic goes there, as a
# JSON envelope {cmd:"packet", type:1, data:<hex aabb frame>} — not raw binary.
_MESSAGE_PREFIX = "clip/message/devices/"
_PROVISIONING_PREFIX = "clip/provisioning/devices/"
_DOWNSTREAM_PREFIX = "lime/devices/"


class Thinq:
    namespaces: tuple[str, ...] = ("clip", "lime")
    device_types: tuple[str, ...] = ("AC",)

    def __init__(self, names: dict[str, str], tap: Callable[[str, dict[str, Any]], None] | None = None) -> None:
        self._names = names  # configured clip id -> display name
        self._tap = tap
        self._publish: Callable[[om.Message], None] | None = None
        # decode (paho's network thread) and Flask workers (fetch_state/default_device)
        # both touch these; LockedDict serializes them, and each update stores a fresh
        # dict so a reader iterating a returned snapshot never races an in-place
        # mutation. Keys are in first-seen order, so default_device() is the last key.
        self._raw: LockedDict[str, dict[int, int]] = LockedDict()  # merged latest TLV values per device
        self._models: LockedDict[str, str] = LockedDict()  # device id -> model kind from its preDeploy payload
        self._asked: LockedDict[str, om.AcState] = LockedDict()  # the state the last command asked for, until a report shows it

    def attach(self, publish: Callable[[om.Message], None]) -> None:
        self._publish = publish

    def decode(self, topic: str, doc: dict[str, Any]) -> tuple[om.Status, ...]:
        if self._tap is not None:
            self._tap(topic, doc)
        model = doc.get("kind")
        if model and model != self._models.get(device_id := topic.rsplit("/", 1)[-1]):
            self._models.update(device_id, lambda cur: model)
            if api.load_fieldmap(model) is None:
                _log.warning("no field map for model %s; capture-only until one exists", model)
        if topic.startswith(_MESSAGE_PREFIX):
            return self._message(topic[len(_MESSAGE_PREFIX) :], doc)
        elif topic.startswith(_PROVISIONING_PREFIX):
            self._provisioning(topic[len(_PROVISIONING_PREFIX) :], doc)
        return ()

    def encode(self, device: om.DeviceEnum, value: Any) -> tuple[om.Message, ...]:
        device_id = str(device.value)
        if self._raw.get(device_id) is None:
            return ()  # unknown/stale clip id: command nothing rather than the wrong AC
        fm = self._fieldmap(device_id)
        if fm is None:
            raise RuntimeError(f"no field map for {device.name}; calibrate its model first")
        state = self.fetch_state(device_id)
        if not isinstance(value, om.AcState):
            raise ValueError(f"AC devices don't support state {value!r}")
        if value == state:
            return ()
        self._asked[device_id] = value
        return (self._packet(device_id, api.build_command(fm, _wire(value))),)

    def snapshot(self) -> tuple[om.DeviceState, ...]:
        return tuple(om.DeviceState(self._device(device_id), asdict(self.fetch_state(device_id)), None) for device_id in self._raw.copy())

    # Retained so a unit that reconnects after an orc restart is prompted on its next
    # subscribe, instead of sitting idle (invisible to devices()/commands) until a manual
    # power-cycle re-provisions it. Diverges from rethink, which never queries and waits
    # for the device to push state.
    def discover(self, messages: Sequence[om.Message]) -> dict[str, tuple[str, frozenset[om.Capability]]]:
        return {}

    def start(self) -> None:
        for device_id in self._names:
            self._send(self._packet(device_id, api.build_query(api.Query.VALUES), retain=True))

    def fetch_state(self, device_id: str) -> om.AcState:
        fm = self._fieldmap(device_id)
        if fm is None:
            return om.AcState()
        return api.state_from_raw(fm, self._raw.get(device_id) or {})

    def devices(self) -> list[str]:
        return list(self._raw.copy())

    def default_device(self) -> str | None:
        return next(reversed(self._raw.copy()), None)

    def _device(self, device_id: str) -> om.Device:
        return om.Device(device_id, self._names.get(device_id, device_id), SOURCE)

    def _fieldmap(self, device_id: str) -> m.Fieldmap | None:
        model = self._models.get(device_id)
        return api.load_fieldmap(model) if model else None

    def _seen(self, device_id: str) -> None:
        self._raw.update(device_id, lambda cur: cur if cur is not None else {})

    def _message(self, device_id: str, doc: dict[str, Any]) -> tuple[om.Status, ...]:
        cmd = doc.get("cmd")
        if cmd == "completeProvisioning_ack":
            self._seen(device_id)
            self._send(self._packet(device_id, api.build_query(api.Query.CAPABILITIES)))
            self._send(self._packet(device_id, api.build_query(api.Query.VALUES)))
        elif cmd == "device_packet":
            fm, pkt = self._fieldmap(device_id), api.frame_tlv(bytes.fromhex(doc.get("data", "")))
            if fm is None or pkt is None:
                return ()  # an unknown model (enable capture to log its raw frames for calibration) or not a TLV frame
            values = {f.type_id: f.value for f in pkt.fields}
            old = self._raw.get(device_id) or {}
            self._raw.update(device_id, lambda cur: {**(cur or {}), **values})
            before, after = api.state_from_raw(fm, old), api.state_from_raw(fm, {**old, **values})
            if old and before != after:
                source = om.Source.EXTERNAL
                if (asked := self._asked.get(device_id)) and after == asked:
                    source = om.Source.ORC
                    self._asked.pop(device_id)
                return (om.Status(self._device(device_id), "state", before, after, source),)
        elif cmd == "req_timesync":
            now = time.gmtime()
            buf = bytes([now.tm_year % 100, now.tm_mon - 1, now.tm_mday, now.tm_hour, now.tm_min, now.tm_sec, (now.tm_wday + 1) % 7])
            self._send(om.Message(_DOWNSTREAM_PREFIX + device_id, _envelope(device_id, "resp_timesync", 1, base64.b64encode(buf).decode())))
        return ()

    def _provisioning(self, device_id: str, doc: dict[str, Any]) -> None:
        device_cmd = doc.get("cmd")
        if device_cmd not in ("preDeploy", "deploy"):
            return  # ignore our own completeProvisioning response echoed back
        self._seen(device_id)
        self._send(om.Message(_DOWNSTREAM_PREFIX + device_id, api.deploy(device_id, int(time.time() * 1000), device_cmd)))

    def _send(self, message: om.Message) -> None:
        if self._publish is not None:
            self._publish(message)

    def _packet(self, device_id: str, frame: bytes, retain: bool = False) -> om.Message:
        return om.Message(_DOWNSTREAM_PREFIX + device_id, _envelope(device_id, "packet", 1, frame.hex()), retain=retain)


# A setpoint frame must carry mode, so a bare on sends whatever else is known with it.
def _wire(state: om.AcState) -> dict[str, object]:
    if state.power == om.OFF:
        return {"mode": "off"}
    values = {"mode": state.mode, "fan_mode": state.fan_mode, "temperature": state.temperature and api.celsius(state.temperature)}
    return {name: v for name, v in values.items() if v is not None}


def _envelope(device_id: str, cmd: str, type_: int, data: str) -> dict[str, Any]:
    return {"did": device_id, "mid": int(time.time() * 1000), "cmd": cmd, "type": type_, "data": data}
