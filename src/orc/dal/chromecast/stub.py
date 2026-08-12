from orc import model as m

_volumes: dict[m.DeviceEnum, int] = {}
_content: dict[m.DeviceEnum, str] = {}


def fetch_state(device: m.DeviceEnum) -> m.SoundState:
    return m.SoundState(what=device, content=_content.get(device), volume=_volumes.get(device, 0))


def fetch_youtube_stream_metadata(id: str) -> tuple[str, str]:
    return ("", "Audio Stream")


def pause(device: m.DeviceEnum) -> None:
    pass


def play(device: m.DeviceEnum, stream_url: str, title: str) -> None:
    _content[device] = stream_url


def resume(device: m.DeviceEnum) -> None:
    pass


def stop(device: m.DeviceEnum) -> None:
    _content.pop(device, None)


def set_volume(device: m.DeviceEnum, lvl: int) -> None:
    _volumes[device] = lvl


def reset() -> None:
    _volumes.clear()
    _content.clear()
