from dataclasses import fields
from typing import Any

from orc import model as m
from orc.model import AppContext, DeviceStatus
from orc_extras.lg_ac.dal.mqtt.interfaces import Transport
from orc_extras.lg_ac.dal.mqtt.thinq import SOURCE
from orc_extras.lg_ac.model import LogSource


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


# an external move is logged by the external plugin; this line is orc's own answer
def _on_change(ctx: AppContext, device: m.Device, attribute: str, old: Any, new: Any) -> None:
    if device.source != SOURCE or attribute != "state":
        return
    changes = [
        f"{f.name} {b} → {a}"
        for f in fields(new)
        if f.compare and (b := getattr(old, f.name)) is not None and b != (a := getattr(new, f.name))
    ]
    if not changes:
        return
    ctx.api.log(LogSource.LG_AC, f"AC {str(device.id)[:8]}: {', '.join(changes)}", m.Broker(id=str(device.id), source=SOURCE, value=new))
