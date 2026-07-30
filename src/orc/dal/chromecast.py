import socket
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import pychromecast
import yt_dlp

from orc import model as m
from orc.decorators import requires_enabled, silence_fd

_YDL_OPTS: dict[str, Any] = {
    "format": "bestaudio/best",  # Request the highest quality audio stream
    "quiet": True,
    "no_warnings": True,
}


_PLAYING_STATES: tuple[str, ...] = ("PLAYING", "BUFFERING", "PAUSED")


@requires_enabled(lambda device: m.SoundState(what=device, content=None, volume=0))
def fetch_state(device: m.DeviceEnum) -> m.SoundState:
    with _cast(device, timeout=5, tries=1) as cast:
        time.sleep(0.5)
        ms = cast.media_controller.status
        content = ms.content_id if ms and ms.player_state in _PLAYING_STATES else None
        return m.SoundState(
            what=device,
            content=_strip_googlevideo_params(content) if content else None,
            volume=int(cast.status.volume_level * 100),
        )


@requires_enabled(lambda *_: ("", "Audio Stream"))
def fetch_youtube_stream_metadata(id: str) -> tuple[str, str]:
    with yt_dlp.YoutubeDL(_YDL_OPTS) as ydl:
        info = ydl.extract_info(id, download=False)
        return info["url"], info.get("title", "Audio Stream")


@requires_enabled(None)
def pause(device: m.DeviceEnum) -> None:
    with _cast(device) as cast:
        cast.media_controller.update_status()
        time.sleep(1)
        if cast.media_controller.status.player_state in ("PLAYING", "BUFFERING"):
            cast.media_controller.pause()


@requires_enabled(None)
def play(device: m.DeviceEnum, stream_url: str, title: str) -> None:
    with _cast(device) as cast:
        mc = cast.media_controller
        # Reset so play_media loads into a fresh receiver. silence_fd(2) swallows
        # pychromecast's "no session is active" warning when nothing is playing.
        with silence_fd(2), suppress(Exception):
            mc.stop()
        if cast.status.app_id:
            cast.quit_app()
            time.sleep(1)
        mc.play_media(stream_url, "audio/mp3", title=title)
        mc.block_until_active(timeout=10)


@requires_enabled(None)
def resume(device: m.DeviceEnum) -> None:
    with _cast(device) as cast:
        cast.media_controller.update_status()
        time.sleep(1)
        if cast.media_controller.status.player_state == "PAUSED":
            cast.media_controller.play()


@requires_enabled(None)
def stop(device: m.DeviceEnum) -> None:
    with _cast(device) as cast:
        cast.quit_app()
        time.sleep(1)


@requires_enabled(None)
def set_volume(device: m.DeviceEnum, lvl: int) -> None:
    with _cast(device) as cast:
        cast.set_volume(lvl / 100)
        time.sleep(1)


@contextmanager
def _cast(device: m.DeviceEnum, **kwargs: Any) -> Iterator[Any]:
    kwargs.setdefault("timeout", 5)
    ip = socket.gethostbyname(device.value)
    # pychromecast accepts None for uuid/model/name at runtime; its stub declares stricter tuple types
    cast = pychromecast.get_chromecast_from_host((ip, 8009, None, None, None), **kwargs)  # type: ignore[arg-type]
    try:
        cast.wait(timeout=kwargs.get("timeout"))
        yield cast
    finally:
        cast.disconnect(timeout=2)


def _strip_googlevideo_params(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.hostname or not parsed.hostname.endswith("googlevideo.com"):
        return url
    vid_id = parse_qs(parsed.query).get("id", [None])[0]
    query = urlencode({"id": vid_id}) if vid_id is not None else ""
    return urlunparse(parsed._replace(query=query))
