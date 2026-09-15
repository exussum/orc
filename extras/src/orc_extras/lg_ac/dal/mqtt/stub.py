from orc_extras.lg_ac.model import ACState

_states: dict[str, ACState] = {}
_devices: list[str] = []
_default: str | None = None
published: list[tuple[str, dict[str, object]]] = []


def reset(states: dict[str, ACState] | None = None, devices: list[str] | None = None, default: str | None = None) -> None:
    global _default
    _states.clear()
    _states.update(states or {})
    _devices[:] = devices or []
    _default = default
    published.clear()


def fetch_state(device_id: str) -> ACState:
    return _states.get(device_id, ACState())


def devices() -> list[str]:
    return list(_devices)


def default_device() -> str | None:
    return _default


def publish_command(device_id: str, values: dict[str, object]) -> None:
    published.append((device_id, values))
