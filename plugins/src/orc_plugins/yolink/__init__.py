"""YoLink leak-sensor integration.

Registers the ``Leak`` device type, a status capture, and a boot-time MQTT client.
Imports of orc.api/orc.view are deferred into the functions that use them, because
this package is imported during orc's config load (before api.py is importable) — a
top-level import would be circular.
"""

from functools import partial
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orc.plugins import PluginCtx

from orc_plugins.yolink import plugins

from orc import model as m

_SIGNAL_WEAK_THRESHOLD = -90


class Msg:
    CONNECTED = "YoLink {name} connected"
    DISCONNECTED = "YoLink {name} disconnected"
    WATER_DETECTED = "Water detected in {name}"
    WATER_CLEARED = "Water cleared in {name}"
    LOW_BATTERY = "Low battery on {name} ({battery})"
    BATTERY_RESTORED = "Battery restored on {name} ({battery})"
    WEAK_SIGNAL = "Weak signal on {name} ({signal} dBm)"
    SIGNAL_RESTORED = "Signal restored on {name} ({signal} dBm)"
    INTERVAL_CHANGED = "Report interval for {name} changed to {interval}s"
    OFFLINE = "{name} offline"
    ONLINE = "{name} online"


def _on_transition(ctx: "PluginCtx", name: str, kind: str, old: Any, new: Any) -> None:
    api = ctx.api
    msg = None
    if kind == "connection" and old is not None:
        msg = (Msg.CONNECTED if new == "connected" else Msg.DISCONNECTED).format(name=name)
    elif kind == "leak" and new in (plugins.STATE_WET, plugins.STATE_DRY):
        msg = (Msg.WATER_DETECTED if new == plugins.STATE_WET else Msg.WATER_CLEARED).format(name=name)
        if new == plugins.STATE_WET:
            api.log(api.local_now(), m.LogSource.PLUGIN, msg)
            api.play_text(msg, level=m.AUDIO_FATAL)
            return
    elif kind == "battery":
        old_critical = old is not None and old.is_critical
        if new.is_critical and not old_critical:
            msg = Msg.LOW_BATTERY.format(name=name, battery=new.value)
        elif old_critical and not new.is_critical:
            msg = Msg.BATTERY_RESTORED.format(name=name, battery=new.value)
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


def setup(ctx: "PluginCtx") -> None:
    ctx.api.add_state_provider("Leak Sensors", leak_state)
    plugins.set_transition_callback(partial(_on_transition, ctx))
    plugins.start()


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
            "battery": s.battery.value if s.battery is not None else None,
            "signal": s.signal,
            "interval": s.interval,
            "last_change": s.last_change,
        }
        for s in plugins.snapshot()
    ]


def declare(declarations: Any) -> None:
    declarations.declare(
        device_types=["Leak"],
        setup=[setup],
        button_labels={"Test Leak Sensor": "Test {device}"},
    )
