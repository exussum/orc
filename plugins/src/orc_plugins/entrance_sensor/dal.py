import dataclasses
from typing import Any

import requests

import orc as config
from orc import model as m
from orc.dal.decorators import requires_enabled


@dataclasses.dataclass(frozen=True)
class SensorState:
    name: str
    device_id: int
    battery: m.BatteryLevel | None = None
    attributes: dict[str, Any] = dataclasses.field(default_factory=dict)


@requires_enabled(lambda name, device_id: SensorState(name=name, device_id=device_id))
def fetch_state(name: str, device_id: int) -> SensorState:
    resp = requests.get(
        f"{config.config.hubitat_url}/devices/{device_id}{config.config.secrets.access_token}",
        timeout=config.config.http_timeout,
    )
    resp.raise_for_status()

    attrs = {a["name"]: a["currentValue"] for a in resp.json()["attributes"]}
    return SensorState(name=name, device_id=device_id, battery=m.BatteryLevel.from_fraction(attrs["battery"], 100), attributes=attrs)
