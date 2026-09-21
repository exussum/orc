import dataclasses
import logging
import os
import signal
import threading
import time
from collections.abc import Callable
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING, Any, Literal, cast

import orc_extras.yolink
from orc import model as m
from orc.collections import LockedDict
from orc_extras.yolink.dal.interfaces import CloudBackend

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

# Flap suppression: the real backend auto-reconnects transient drops, so only treat
# the connection as down once we've seen several disconnects in a short window.
_FLAP_WINDOW_SEC = 60
_FLAP_THRESHOLD = 3


def states_for(leak_devices: Any) -> LockedDict[str, SensorState]:
    return LockedDict({device.value: SensorState(name=device.label, device_id=device.value) for device in leak_devices})


def _states(ctx: m.AppContext) -> LockedDict[str, SensorState]:
    return cast(LockedDict[str, SensorState], ctx.plugin_state[orc_extras.yolink])


def _leak_devices(ctx: m.AppContext) -> Any:
    return ctx.config.devices.Leak


def start(ctx: m.AppContext, backend: CloudBackend, on_transition: TransitionCallback) -> None:
    if not len(_leak_devices(ctx)):
        _log.info("yolink: no Leak devices in config.orc, skipping")
        return
    threading.Thread(target=partial(_run, ctx, backend, on_transition), name="yolink-mqtt", daemon=True).start()


def snapshot(ctx: m.AppContext) -> list[SensorState]:
    sensors = _states(ctx).copy()
    return [sensors.get(device.value) or SensorState(name=device.label, device_id=device.value) for device in _leak_devices(ctx)]


def simulate_transition(ctx: m.AppContext, name: str, on_transition: TransitionCallback) -> bool:
    states = _states(ctx)
    sensor = next((s for s in states.copy().values() if s.name == name), None)
    if sensor is None:
        return False
    device_id = sensor.device_id
    prev = sensor.state if sensor.state in (STATE_DRY, STATE_WET) else STATE_DRY

    if states.update(device_id, _transition_to(ctx, STATE_WET)) is None:
        return False
    _fire(on_transition, "leak", name, prev, STATE_WET)

    def _revert() -> None:
        time.sleep(5)
        if states.update(device_id, _transition_to(ctx, STATE_DRY, require=STATE_WET)) is not None:
            _fire(on_transition, "leak", name, STATE_WET, STATE_DRY)

    threading.Thread(target=_revert, name=f"yolink-test-revert-{name}", daemon=True).start()
    return True


# --- client lifecycle ---


def _run(ctx: m.AppContext, backend: CloudBackend, on_transition: TransitionCallback) -> None:
    cfg = ctx.config
    disconnect_times: list[float] = []
    on_connection = partial(_on_connection, ctx, on_transition, disconnect_times)
    on_report = partial(_on_report, ctx, on_transition)
    try:
        while True:
            try:
                access_token, expires_in = backend.authenticate(cfg.secrets, cfg.settings.http_timeout)
            except Exception:
                _log.exception("yolink: auth failed; retrying in 60s")
                time.sleep(60)
                continue
            try:
                _hydrate_states(ctx, backend, access_token)
            except Exception:
                _log.exception("yolink: hydrate failed; continuing without initial state")
            try:
                session = backend.connect(access_token, on_connection, on_report, cfg.settings.http_timeout)
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


def _on_connection(ctx: m.AppContext, on_transition: TransitionCallback, disconnect_times: list[float], connected: bool) -> None:
    if connected:
        _set_connected(ctx, on_transition, True)
        return
    now = time.time()
    cutoff = now - _FLAP_WINDOW_SEC
    disconnect_times[:] = [t for t in disconnect_times if t >= cutoff]
    disconnect_times.append(now)
    if len(disconnect_times) >= _FLAP_THRESHOLD:
        _set_connected(ctx, on_transition, False)


def _on_report(ctx: m.AppContext, on_transition: TransitionCallback, device_id: str, data: dict[str, Any]) -> None:
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
        return dataclasses.replace(current, last_change=ctx.api.local_now(), **changes)

    _states(ctx).update(device_id, apply)
    for kind, old, new in captured["transitions"]:
        _fire(on_transition, kind, captured["name"], old, new)


# --- state transitions ---


def _hydrate_states(ctx: m.AppContext, backend: CloudBackend, access_token: str) -> None:
    device_ids = [device.value for device in _leak_devices(ctx)]
    for device_id, data in backend.fetch_leak_states(access_token, device_ids, ctx.config.settings.http_timeout).items():
        state = data["state"]
        _update_sensor(
            ctx,
            device_id,
            online=data.get("online"),
            state=state.get("state"),
            battery=m.BatteryLevel.from_fraction(state["battery"], 4),
            interval=state.get("interval"),
            signal=(state.get("loraInfo") or {}).get("signal"),
        )


def _update_sensor(ctx: m.AppContext, device_id: str, **fields: Any) -> None:
    _states(ctx).update(device_id, lambda current: dataclasses.replace(current, **fields) if current else None)


def _transition_to(ctx: m.AppContext, new_state: str, require: str | None = None) -> Callable[[SensorState | None], SensorState | None]:
    def fn(current: SensorState | None) -> SensorState | None:
        if current is None or (require is not None and current.state != require):
            return None
        return dataclasses.replace(current, state=new_state, last_change=ctx.api.local_now())

    return fn


def _set_connected(ctx: m.AppContext, on_transition: TransitionCallback, connected: bool) -> None:
    # collect (name, prior state) inside the atomic update, fire after releasing the lock
    fired: list[tuple[str, str]] = []

    def apply(current: SensorState | None) -> SensorState | None:
        if current is None or current.connected == connected:
            return None
        fired.append((current.name, "connected" if current.connected else "disconnected"))
        return dataclasses.replace(current, connected=connected)

    states = _states(ctx)
    for device_id in states.copy().keys():
        states.update(device_id, apply)
    for name, old in fired:
        _fire(on_transition, "connection", name, old, "connected" if connected else "disconnected")


def _fire(on_transition: TransitionCallback, kind: TransitionKind, name: str, old: Any, new: Any) -> None:
    if old != new:
        try:
            on_transition(name, kind, old, new)
        except Exception:
            _log.exception("yolink transition callback failed")


if TYPE_CHECKING:
    from orc_extras.yolink.dal import stub, yosmart

    _real: CloudBackend = yosmart
    _stub: CloudBackend = stub
