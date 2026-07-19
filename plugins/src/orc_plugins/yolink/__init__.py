"""YoLink leak-sensor integration.

Registers the ``Leak`` device type, a status capture, and a boot-time MQTT client.
Imports of orc.api/orc.view are deferred into the functions that use them, because
this package is imported during orc's config load (before api.py is importable) — a
top-level import would be circular.
"""

from typing import Any

from orc_plugins.yolink import dal

_BATTERY_LOW_THRESHOLD = 1
_SIGNAL_WEAK_THRESHOLD = -90


class Msg:
    CONNECTED = "YoLink {name} connected"
    DISCONNECTED = "YoLink {name} disconnected"
    WATER_DETECTED = "Water detected in {name}"
    WATER_CLEARED = "Water cleared in {name}"
    LOW_BATTERY = "Low battery on {name} ({battery}/4)"
    BATTERY_RESTORED = "Battery restored on {name} ({battery}/4)"
    WEAK_SIGNAL = "Weak signal on {name} ({signal} dBm)"
    SIGNAL_RESTORED = "Signal restored on {name} ({signal} dBm)"
    INTERVAL_CHANGED = "Report interval for {name} changed to {interval}s"
    OFFLINE = "{name} offline"
    ONLINE = "{name} online"


def _on_transition(name: str, kind: str, old: Any, new: Any) -> None:
    from orc import api
    from orc import model as m

    msg = None
    if kind == "connection" and old is not None:
        msg = (Msg.CONNECTED if new == "connected" else Msg.DISCONNECTED).format(name=name)
    elif kind == "leak" and new in (dal.STATE_WET, dal.STATE_DRY):
        msg = (Msg.WATER_DETECTED if new == dal.STATE_WET else Msg.WATER_CLEARED).format(name=name)
        if new == dal.STATE_WET:
            api.log(api.local_now(), m.LogSource.PLUGIN, msg)
            api.play_text(msg, level=m.AUDIO_FATAL)
            return
    elif kind == "battery":
        old_low = old is not None and old <= _BATTERY_LOW_THRESHOLD
        new_low = new <= _BATTERY_LOW_THRESHOLD
        if new_low and not old_low:
            msg = Msg.LOW_BATTERY.format(name=name, battery=new)
        elif old_low and not new_low:
            msg = Msg.BATTERY_RESTORED.format(name=name, battery=new)
    elif kind == "signal":
        old_weak = old is not None and old <= _SIGNAL_WEAK_THRESHOLD
        new_weak = new <= _SIGNAL_WEAK_THRESHOLD
        if new_weak and not old_weak:
            msg = Msg.WEAK_SIGNAL.format(name=name, signal=new)
        elif old_weak and not new_weak:
            msg = Msg.SIGNAL_RESTORED.format(name=name, signal=new)
    elif kind == "interval" and old is not None:
        msg = Msg.INTERVAL_CHANGED.format(name=name, interval=new)
    elif kind == "online" and old is not None:
        msg = (Msg.ONLINE if new else Msg.OFFLINE).format(name=name)

    if msg:
        api.log(api.local_now(), m.LogSource.PLUGIN, msg)
        api.play_text(msg)


def start() -> None:
    dal.set_transition_callback(_on_transition)
    dal.start()


def leak_state() -> list[dict[str, Any]]:
    """Per-sensor state rows for core's generic state renderer (needs a "name" key).

    Each row carries ``action`` so core renders the name as a clickable runner that
    hits ``/api/run/Test Leak Sensor?device=<name>`` — the config-declared plugin.
    """
    return [
        {
            "name": s.name,
            "action": "Test Leak Sensor",
            "state": s.state,
            "connected": s.connected,
            "online": s.online,
            "battery": s.battery,
            "signal": s.signal,
            "interval": s.interval,
            "last_change": s.last_change,
        }
        for s in dal.snapshot()
    ]


def register(core: Any) -> None:
    core.register_plugin(
        device_types=["Leak"],
        state_providers={"Leak Sensors": leak_state},
        startup=[start],
        button_labels={"Test Leak Sensor": "Test {device}"},
    )
