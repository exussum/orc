from orc.model import AcState

_states: dict[str, AcState] = {}
_devices: list[str] = []
_default: str | None = None


def reset(
    states: dict[str, AcState] | None = None,
    devices: list[str] | None = None,
    default: str | None = None,
) -> None:
    global _states, _devices, _default
    _states = dict(states or {})
    _devices = list(devices or [])
    _default = default


def fetch_state(device_id: str) -> AcState:
    return _states.get(device_id, AcState())


def devices() -> list[str]:
    return list(_devices)


def default_device() -> str | None:
    return _default
