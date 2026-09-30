from dataclasses import dataclass
from typing import Any

from orc import model as m
from orc.model import AcCommand, AcMode, AcState, AppContext, DeviceStatus
from orc_extras.lg_ac import api
from orc_extras.lg_ac.dal.mqtt.interfaces import Transport
from orc_extras.lg_ac.model import ACState, LogSource


@dataclass(frozen=True)
class Ac:
    transport: Transport

    def command(self, device: Any, state: str | None, mode: str | None, fan: str | None, temp: int | None) -> None:
        """Drive the AC from orc's /device/ page AC card (mode/fan/temp in °F)."""
        transport = self.transport
        device_id = str(device.value)
        if device_id not in transport.devices():
            return  # unknown/stale clip id: command nothing rather than the wrong AC
        if state == "off":
            transport.publish_command(device_id, {"mode": "off"})
            return
        # a setpoint frame must carry mode, so an omitted mode keeps the device's current one
        values: dict[str, object] = {"mode": mode or transport.fetch_state(device_id).mode or "cool"}
        if fan:
            values["fan_mode"] = fan
        if temp is not None:
            values["temperature"] = api.celsius(temp)
        transport.publish_command(device_id, values)

    def state(self, device: Any) -> AcState | None:
        transport = self.transport
        state = transport.fetch_state(str(device.value))  # unknown/stale id yields an empty state
        if state.power is None:
            return None
        elif state.power == "OFF":
            return AcState.OFF
        return AcState.__members__.get((state.mode or "").upper(), AcState.ON)

    def temperature(self, device: Any) -> int | None:
        transport = self.transport
        state = transport.fetch_state(str(device.value))
        return None if state.temperature is None or state.power == "OFF" else round(state.temperature)


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


def _on_event(ctx: AppContext, device_id: str, msg: str, state: ACState) -> None:
    value: str | AcCommand | None
    if state.power == "OFF":
        value = m.OFF
    elif state.mode and state.mode in AcMode and state.fan_mode and state.temperature is not None:
        value = AcCommand(AcMode(state.mode), state.fan_mode, round(state.temperature))
    else:
        value = None
    ctx.api.log(LogSource.LG_AC, msg, m.Broker(id=device_id, source="lg_ac", value=value))
