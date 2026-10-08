from orc.model import AppContext, DeviceStatus
from orc_extras.lg_ac.dal.mqtt.interfaces import Transport


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
