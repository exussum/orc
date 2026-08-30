from orc import model as m
from orc.dal.chromecast import MAX_CHARS

_volumes: dict[m.DeviceEnum, int] = {}
_content: dict[m.DeviceEnum, str] = {}
_announced: list[str] = []


def fetch_state(device: m.DeviceEnum) -> m.SoundState:
    return m.SoundState(what=device, content=_content.get(device), volume=_volumes.get(device, 0))


def fetch_youtube_stream_metadata(id: str) -> tuple[str, str]:
    return ("", "Audio Stream")


def speak(device: m.DeviceEnum, text: str) -> None:
    if len(text) > MAX_CHARS:
        raise ValueError(f"Announcement text exceeds {MAX_CHARS} characters: {len(text)}")
    _announced.append(text)
    _content[device] = text


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
    _announced.clear()
