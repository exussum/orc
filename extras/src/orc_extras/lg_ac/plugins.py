from typing import Any

from orc import model as m
from orc.model import AcCommand, AcMode, AppContext, DeviceStatus
from orc_extras.lg_ac.dal.mqtt.interfaces import Transport
from orc_extras.lg_ac.dal.mqtt.thinq import SOURCE
from orc_extras.lg_ac.model import ACState, LogSource


def _ac_status(transport: Transport, ctx: AppContext) -> list[DeviceStatus]:
    rows = []
    connected = transport.devices()
    for device in ctx.config.devices.AC:
        device_id = str(device.value)
        state = transport.fetch_state(device_id)
        rows.append(
            DeviceStatus(
                name=device.name,
                label=device.label,
                details={
                    "connected": device_id in connected,
                    "power": state.power,
                    "mode": state.mode,
                    "fan": state.fan_mode,
                    "target": state.temperature,
                    "current": state.current_temperature,
                },
            )
        )
    return rows


def _on_change(ctx: AppContext, device: m.Device, attribute: str, old: Any, new: Any) -> None:
    if device.source != SOURCE or attribute != "state":
        return
    changes = [
        f"{field} {b} → {a}"
        for field, b, a in zip(new._fields, old, new, strict=True)
        if field != "current_temperature" and b is not None and b != a
    ]
    if not changes:
        return
    ctx.api.log(
        LogSource.LG_AC, f"AC {str(device.id)[:8]}: {', '.join(changes)}", m.Broker(id=str(device.id), source=SOURCE, value=_value(new))
    )


def _value(state: ACState) -> str | AcCommand | None:
    if state.power == m.OFF:
        return m.OFF
    elif state.mode and state.mode in AcMode and state.fan_mode and state.temperature is not None:
        return AcCommand(AcMode(state.mode), state.fan_mode, round(state.temperature))
    return None
