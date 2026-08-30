import socket
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import pychromecast
import yt_dlp

from orc import model as m
from orc.dal.chromecast import MAX_CHARS
from orc.decorators import silence_fd

_YDL_OPTS: dict[str, Any] = {
    "format": "bestaudio/best",  # Request the highest quality audio stream
    "quiet": True,
    "no_warnings": True,
}


_PLAYING_STATES: tuple[str, ...] = ("PLAYING", "BUFFERING", "PAUSED")


def fetch_state(device: m.DeviceEnum) -> m.SoundState:
    with _cast(device, timeout=5, tries=1) as cast:
        time.sleep(0.5)
        if cast.status is None:  # wait() timed out: device unreachable
            return m.SoundState(what=device, content=None, volume=0)
        # A fresh connection doesn't reliably get an unsolicited media status broadcast
        # in time; request one explicitly and give it a moment to arrive.
        cast.media_controller.update_status()
        time.sleep(0.5)
        ms = cast.media_controller.status
        content = None
        if ms and ms.player_state in _PLAYING_STATES:
            content = ms.title or (_strip_googlevideo_params(ms.content_id) if ms.content_id else None)
        return m.SoundState(
            what=device,
            content=content,
            volume=int(cast.status.volume_level * 100),
        )


def fetch_youtube_stream_metadata(id: str) -> tuple[str, str]:
    with yt_dlp.YoutubeDL(_YDL_OPTS) as ydl:
        info = ydl.extract_info(id, download=False)
        return info["url"], info.get("title", "Audio Stream")


def speak(device: m.DeviceEnum, text: str) -> None:
    if len(text) > MAX_CHARS:
        raise ValueError(f"Announcement text exceeds {MAX_CHARS} characters: {len(text)}")
    url = "https://translate.google.com/translate_tts?" + urlencode({"ie": "UTF-8", "q": text, "tl": "en", "client": "tw-ob"})
    with _cast(device) as cast:
        mc = cast.media_controller
        for attempt in range(2):
            mc.play_media(url, "audio/mp3", title=text)
            mc.block_until_active(timeout=5)
            if mc.status.player_state != "IDLE" or mc.status.idle_reason != "ERROR":
                return
        raise RuntimeError(f"{device.name}: Chromecast failed to load announcement {text!r}")


def pause(device: m.DeviceEnum) -> None:
    with _cast(device) as cast:
        cast.media_controller.update_status()
        time.sleep(1)
        if cast.media_controller.status.player_state in ("PLAYING", "BUFFERING"):
            cast.media_controller.pause()


def play(device: m.DeviceEnum, stream_url: str, title: str) -> None:
    with _cast(device) as cast:
        # Reset so play_media loads into a fresh receiver. silence_fd(2) swallows
        # pychromecast's "no session is active" warning when nothing is playing.
        with silence_fd(2), suppress(Exception):
            cast.media_controller.stop()
        if cast.status.app_id:
            cast.quit_app()
            time.sleep(1)

    # Reconnect rather than reusing `cast`/`mc` across the quit_app transition:
    # block_until_active only reports correctly the first time it's asked on a
    # given instance (https://github.com/home-assistant-libs/pychromecast/issues/335).
    with _cast(device) as cast:
        mc = cast.media_controller
        mc.play_media(stream_url, "audio/mp3", title=title)
        mc.block_until_active(timeout=10)
        if mc.status.player_state == "IDLE" and mc.status.idle_reason == "ERROR":
            raise RuntimeError(f"{device.name}: Chromecast failed to load {title!r}")


def resume(device: m.DeviceEnum) -> None:
    with _cast(device) as cast:
        cast.media_controller.update_status()
        time.sleep(1)
        if cast.media_controller.status.player_state == "PAUSED":
            cast.media_controller.play()


def stop(device: m.DeviceEnum) -> None:
    with _cast(device) as cast:
        cast.quit_app()
        time.sleep(1)


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
