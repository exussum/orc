"""Hubitat MQTT Export adapter.

Turns the hub's retained per-device JSON documents on
``hubitat/<hub-uuid>/devices/<id>`` into device statuses and light commands into
publishes; the hub uuid is captured from the first message rather than
configured. Everything but the past-log scrape flows through here.
"""

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from orc import model as m
from orc.collections import LockedDict
from orc.dal import sqlite

REQUIRED_SECRETS: dict[str, Callable[[str], Any]] = {}

_log = logging.getLogger(__name__)

_SOURCE = "hubitat"
# The commandable attributes, in the hub's spelling: the ones a pending command can
# explain and an unexplained move of which is reported as external.
_WATCHED = ("switch", "level", "hue", "saturation", "colorTemperature")


class HubitatSource(m.SourceEnum):
    HUBITAT = "hubitat"


namespaces: tuple[str, ...] = ("hubitat",)
device_types: tuple[str, ...] = ("Light", "Button", "Sensor")

hub_id: str | None = None  # written only by the mqtt thread
_devices: LockedDict[str, m.DeviceState] = LockedDict()

# Command round-trip measurement: encode stamps the command topic at send, and
# the broker echoing our own publish back on the hubitat/# subscription pops it.
# Keyed by topic, so the size is bounded by distinct (device, command) pairs; an
# entry whose echo never arrives (broker down at publish) is overwritten by the
# topic's next send. Round trips fold into orc_durations, keyed by command topic.
_command_sent: LockedDict[str, float] = LockedDict()

# External-control detection: what the last command asked of each light, held until
# a report shows it, so a change nothing asked for (Google Home, a physical switch)
# is reported as external. Same shape as the ThinQ adapter's _asked.
_commanded: LockedDict[str, m.LightState] = LockedDict()


def attach(publish: Callable[[m.Message], None]) -> None:
    pass


def decode(topic: str, doc: dict[str, Any]) -> tuple[m.Status, ...]:
    global hub_id
    parts = topic.split("/")
    if len(parts) < 4 or parts[2] != "devices":
        return ()  # location/variables topics
    hub_id = parts[1]
    kind = parts[4] if len(parts) > 4 else None
    if kind is None:
        return _document(topic, doc)
    elif kind == "button" and doc:  # the empty clearing publish follows each event
        return _button(topic, doc)
    elif kind == "commands":
        _echo(topic)
    return ()


def encode(device: m.DeviceEnum, command: Any) -> tuple[m.Message, ...]:
    if hub_id is None:
        raise RuntimeError(f"hub not yet seen; cannot command {device.name}")
    asked = _light_state(device, command)
    current = _current(device.value)
    if current is not None and asked == current:
        return ()
    _commanded[device.value] = asked
    message = _hub_command(hub_id, device.value, asked)
    _command_sent[message.topic] = time.monotonic()
    return (message,)


def snapshot() -> tuple[m.DeviceState, ...]:
    return tuple(sorted(_devices.values(), key=lambda state: state.device.id))


def start() -> None:
    pass


def discover(messages: Sequence[m.Message]) -> dict[str, tuple[str, frozenset[m.Capability]]]:
    found: dict[str, tuple[str, frozenset[m.Capability]]] = {}
    retained: set[str] = set()
    for message in messages:
        parts = message.topic.split("/")
        state = parse_document(message.payload) if len(parts) == 4 and parts[2] == "devices" and isinstance(message.payload, dict) else None
        if state is not None:
            capabilities = set()
            if "level" in state.attributes:
                capabilities.add(m.Capability.change_level)
            if "hue" in state.attributes:
                capabilities.add(m.Capability.color)
            found[state.device.name] = (state.device.id, frozenset(capabilities))
            if message.retain:
                retained.add(state.device.name)
    if not found:
        raise RuntimeError("mqtt: device discovery found no device documents; broker unreachable or MQTT credentials missing")

    # the cache, boot discovery and every reconnect lean on the hub publishing retained documents
    if unretained := sorted(found.keys() - retained):
        raise RuntimeError(f"mqtt: device documents are not retained for {', '.join(unretained)}; turn retain on in the MQTT Export app")
    return found


def parse_document(doc: dict[str, Any]) -> m.DeviceState | None:
    try:
        return m.DeviceState(
            m.Device(str(doc["id"]), doc["name"], _SOURCE),
            attributes={a["name"]: a["value"] for a in doc["attributes"]},
            last_activity=doc.get("lastActivity"),
        )
    except ValueError, KeyError, TypeError:
        return None


# State is never an event: first sightings (the retained flood at boot) and replays
# (a document identical to the cached one — reconnect floods, hub republish after
# reboot) update the cache but yield nothing. No other dedup: the hub regenerates
# the document only when something happens, so a changed document yields every
# attribute, unchanged ones included (old == new); consumers filter for what they
# care about. A commandable attribute that moved has source EXTERNAL when no pending
# command explains it and ORC when one does; everything else the device itself reports.
def _document(topic: str, doc: dict[str, Any]) -> tuple[m.Status, ...]:
    state = parse_document(doc)
    if state is None:
        _log.error("mqtt: bad device document on %s: %r", topic, doc)
        return ()
    id = state.device.id
    old, _devices[id] = _devices.get(id), state
    if old is None or old == state:
        return ()
    asked = _commanded.get(id)
    sources: dict[str, m.SourceEnum] = {}
    for attribute in _WATCHED:
        before, after = old.attributes.get(attribute), state.attributes.get(attribute)
        if asked is not None and _explains(asked, attribute, after):
            sources[attribute] = m.Source.ORC
        elif before is not None and after is not None and _record({attribute: before}) != _record({attribute: after}):
            sources[attribute] = m.Source.EXTERNAL
    if asked is not None and asked == _record(state.attributes):
        _commanded.pop(id)
    return tuple(
        m.Status(state.device, a, old.attributes.get(a), v, sources.get(a, HubitatSource.HUBITAT)) for a, v in state.attributes.items()
    )


# A press is the one status with nothing before it: (device, event type, None,
# button number), from a dedicated event message that is never retained or replayed.
def _button(topic: str, doc: dict[str, Any]) -> tuple[m.Status, ...]:
    try:
        device_id, button, event_type = topic.split("/")[3], int(doc["button"]), doc["event_type"]
    except ValueError, KeyError, TypeError:
        _log.exception("mqtt: bad button event on %s", topic)
        return ()
    known = _devices.get(device_id)
    if known is None:
        return ()
    return (m.Status(known.device, event_type, None, button, HubitatSource.HUBITAT),)


def _echo(topic: str) -> None:
    sent = _command_sent.pop(topic)
    if sent is not None:
        sqlite.update_avg(topic, time.monotonic() - sent)


def _light_state(device: m.DeviceEnum, command: Any) -> m.LightState:
    if isinstance(command, m.LightState):
        if command.color_mode and m.Capability.color not in device.capabilities:
            raise ValueError(f"{device.name} does not support colour; cannot set {command}")
        return command
    elif command == m.ON:
        return m.LightState(switch=m.ON)
    elif command == m.OFF:
        return m.LightState(switch=m.OFF)
    elif isinstance(command, int) and m.Capability.change_level in device.capabilities:
        return m.LightState(switch=m.ON, level=command) if command else m.LightState(switch=m.OFF)
    elif command in (0, 100):
        return m.LightState(switch=m.ON if command else m.OFF)
    raise ValueError(f"{device.name} does not support ChangeLevel; cannot set brightness {command}")


def _current(device_id: str) -> m.LightState | None:
    known = _devices.get(device_id)
    return _record(known.attributes) if known is not None else None


def _hub_command(hub_id: str, device_id: str, state: m.LightState) -> m.Message:
    base = f"hubitat/{hub_id}/devices/{device_id}/commands/"
    if state.hue is not None:
        return m.Message(base + "setColor", {"hue": state.hue, "saturation": state.saturation})
    elif state.color_temperature is not None:
        return m.Message(base + "setColorTemperature", str(state.color_temperature))
    elif state.level is not None:
        return m.Message(base + "setLevel", str(state.level))
    return m.Message(base + (m.OFF if state.switch == m.OFF else m.ON), None)


def _explains(asked: m.LightState, attribute: str, reported: Any) -> bool:
    wanted = _hub_attributes(asked).get(attribute)
    return wanted is not None and reported is not None and _record({attribute: wanted}) == _record({attribute: reported})


def _record(attributes: Mapping[str, Any]) -> m.LightState:
    mode = attributes.get("colorMode")
    return m.LightState(
        switch=attributes.get("switch"),
        level=_int(attributes.get("level")),
        hue=_int(attributes.get("hue")),
        saturation=_int(attributes.get("saturation")),
        color_temperature=_int(attributes.get("colorTemperature")),
        color_mode=m.ColorMode(mode) if isinstance(mode, str) and mode in m.ColorMode else None,
    )


def _hub_attributes(state: m.LightState) -> dict[str, Any]:
    return {
        "switch": state.switch,
        "level": state.level,
        "hue": state.hue,
        "saturation": state.saturation,
        "colorTemperature": state.color_temperature,
    }


def _int(value: Any) -> int | None:
    return None if value is None else int(value)
