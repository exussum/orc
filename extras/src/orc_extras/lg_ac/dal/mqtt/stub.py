from orc_extras.lg_ac.model import ACState

_states: dict[str, ACState] = {}
_devices: list[str] = []
_default: str | None = None


def reset(states: dict[str, ACState] | None = None, devices: list[str] | None = None, default: str | None = None) -> None:
    global _default
    _states.clear()
    _states.update(states or {})
    _devices[:] = devices or []
    _default = default


def fetch_state(device_id: str) -> ACState:
    return _states.get(device_id, ACState())


def devices() -> list[str]:
    return list(_devices)


def default_device() -> str | None:
    return _default
