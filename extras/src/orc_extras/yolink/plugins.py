import dataclasses
import logging
import os
import signal
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, cast

import orc as config
import orc_extras.yolink
from orc import model as m
from orc.collections import LockedDict
from orc.loader import resolve_backend
from orc_extras.yolink.dal.interfaces import CloudBackend

# orc.Leak is attached to the orc package at runtime once this plugin registers the
# "Leak" device type; mypy can't see the dynamic attribute, so iterate it via an Any view.
_orc: Any = config

# callback (name, kind, old, new); old/new are arbitrary field values
TransitionKind = Literal["connection", "leak", "battery", "signal", "interval", "online"]
type TransitionCallback = Callable[[str, TransitionKind, Any, Any], None]


@dataclasses.dataclass(frozen=True)
class SensorState:
    name: str
    device_id: str
    connected: bool = False
    state: str | None = None
    battery: m.BatteryLevel | None = None
    signal: int | None = None
    interval: int | None = None
    online: bool | None = None
    last_change: datetime | None = None


STATE_DRY = "normal"
STATE_WET = "alert"

_log = logging.getLogger(__name__)

_states: LockedDict[str, SensorState] = LockedDict()  # device_id -> SensorState
_on_transition: TransitionCallback | None = None

# Flap suppression: the real backend auto-reconnects transient drops, so only treat
# the connection as down once we've seen several disconnects in a short window.
_FLAP_WINDOW_SEC = 60
_FLAP_THRESHOLD = 3
_disconnect_times: list[float] = []


def _backend() -> CloudBackend:
    return cast(CloudBackend, resolve_backend(config.config.plugin_for(orc_extras.yolink).backend))


def start() -> None:
    if not len(_orc.Leak):
        _log.info("yolink: no Leak devices in config.orc, skipping")
        return

    global _states
    _states = LockedDict({device.value: SensorState(name=device.label, device_id=device.value) for device in _orc.Leak})
    threading.Thread(target=_run, name="yolink-mqtt", daemon=True).start()


def set_transition_callback(fn: TransitionCallback) -> None:
    global _on_transition
    _on_transition = fn


def snapshot() -> list[SensorState]:
    sensors = _states.copy()
    return [sensors.get(device.value) or SensorState(name=device.label, device_id=device.value) for device in _orc.Leak]


def simulate_transition(name: str) -> bool:
    sensor = next((s for s in _states.copy().values() if s.name == name), None)
    if sensor is None:
        return False
    device_id = sensor.device_id
    prev = sensor.state if sensor.state in (STATE_DRY, STATE_WET) else STATE_DRY

    if _states.update(device_id, _transition_to(STATE_WET)) is None:
        return False
    _fire("leak", name, prev, STATE_WET)

    def _revert() -> None:
        time.sleep(5)
        if _states.update(device_id, _transition_to(STATE_DRY, require=STATE_WET)) is not None:
            _fire("leak", name, STATE_WET, STATE_DRY)

    threading.Thread(target=_revert, name=f"yolink-test-revert-{name}", daemon=True).start()
    return True


# --- client lifecycle ---


def _run() -> None:
    backend = _backend()
    try:
        while True:
            try:
                access_token, expires_in = backend.authenticate()
            except Exception:
                _log.exception("yolink: auth failed; retrying in 60s")
                time.sleep(60)
                continue
            try:
                _hydrate_states(backend, access_token)
            except Exception:
                _log.exception("yolink: hydrate failed; continuing without initial state")
            try:
                session = backend.connect(access_token, _on_connection, _on_report)
            except Exception:
                _log.exception("yolink: connect failed; retrying in 60s")
                time.sleep(60)
                continue
            # Re-auth a few minutes before the token expires
            time.sleep(max(60, expires_in - 300))
            session.close()
    finally:
        _log.error("yolink: thread exiting; signaling SIGTERM to process for restart")
        os.kill(os.getpid(), signal.SIGTERM)


def _on_connection(connected: bool) -> None:
    if connected:
        _set_connected(True)
        return
    now = time.time()
    cutoff = now - _FLAP_WINDOW_SEC
    _disconnect_times[:] = [t for t in _disconnect_times if t >= cutoff]
    _disconnect_times.append(now)
    if len(_disconnect_times) >= _FLAP_THRESHOLD:
        _set_connected(False)


def _on_report(device_id: str, data: dict[str, Any]) -> None:
    data["battery"] = m.BatteryLevel.from_fraction(data["battery"], 4)

    # collect transitions inside the atomic update, fire after releasing the lock
    captured: dict[str, Any] = {"name": None, "transitions": []}

    def apply(current: SensorState | None) -> SensorState | None:
        if current is None:
            return None
        old = {
            "state": current.state,
            "battery": current.battery,
            "signal": current.signal,
            "interval": current.interval,
            "online": current.online,
        }
        changes = {f: new for f, prev in old.items() if (new := data.get(f)) is not None and new != prev}
        if not changes:
            return None
        captured["name"] = current.name
        captured["transitions"] = [("leak" if f == "state" else f, old[f], new) for f, new in changes.items()]
        return dataclasses.replace(current, last_change=datetime.now(tz=config.config.settings.tz), **changes)

    _states.update(device_id, apply)
    for kind, old, new in captured["transitions"]:
        _fire(kind, captured["name"], old, new)


# --- state transitions ---


def _hydrate_states(backend: CloudBackend, access_token: str) -> None:
    device_ids = [device.value for device in _orc.Leak]
    for device_id, data in backend.fetch_leak_states(access_token, device_ids).items():
        state = data["state"]
        _update_sensor(
            device_id,
            online=data.get("online"),
            state=state.get("state"),
            battery=m.BatteryLevel.from_fraction(state["battery"], 4),
            interval=state.get("interval"),
            signal=(state.get("loraInfo") or {}).get("signal"),
        )


def _update_sensor(device_id: str, **fields: Any) -> None:
    _states.update(device_id, lambda current: dataclasses.replace(current, **fields) if current else None)


def _transition_to(new_state: str, require: str | None = None) -> Callable[[SensorState | None], SensorState | None]:
    def fn(current: SensorState | None) -> SensorState | None:
        if current is None or (require is not None and current.state != require):
            return None
        return dataclasses.replace(current, state=new_state, last_change=datetime.now(tz=config.config.settings.tz))

    return fn


def _set_connected(connected: bool) -> None:
    # collect (name, prior state) inside the atomic update, fire after releasing the lock
    fired: list[tuple[str, str]] = []

    def apply(current: SensorState | None) -> SensorState | None:
        if current is None or current.connected == connected:
            return None
        fired.append((current.name, "connected" if current.connected else "disconnected"))
        return dataclasses.replace(current, connected=connected)

    for device_id in _states.copy().keys():
        _states.update(device_id, apply)
    for name, old in fired:
        _fire("connection", name, old, "connected" if connected else "disconnected")


def _fire(kind: TransitionKind, name: str, old: Any, new: Any) -> None:
    if _on_transition and old != new:
        try:
            _on_transition(name, kind, old, new)
        except Exception:
            _log.exception("yolink transition callback failed")


if TYPE_CHECKING:
    from orc_extras.yolink.dal import stub, yosmart

    _real: CloudBackend = yosmart
    _stub: CloudBackend = stub
