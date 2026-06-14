import socket
import time
from contextlib import contextmanager
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import pychromecast
import yt_dlp

from orc import model as m
from orc.dal._decorators import requires_enabled

_YDL_OPTS = {
    "format": "bestaudio/best",  # Request the highest quality audio stream
    "quiet": True,
    "no_warnings": True,
}


@requires_enabled(lambda device: m.SoundState(what=device, content=None, volume=0))
def fetch_state(device):
    with _cast(device, timeout=5, tries=1) as cast:
        time.sleep(3)
        content = cast.media_controller.status.content_id
        return m.SoundState(
            what=device,
            content=_strip_googlevideo_params(content) if content else None,
            volume=int(cast.status.volume_level * 100),
        )


@requires_enabled(lambda *_: ("", "Audio Stream"))
def fetch_youtube_stream_metadata(id):
    with yt_dlp.YoutubeDL(_YDL_OPTS) as ydl:
        info = ydl.extract_info(id, download=False)
        return info["url"], info.get("title", "Audio Stream")


@requires_enabled(None)
def pause(device):
    with _cast(device) as cast:
        cast.media_controller.update_status()
        time.sleep(1)
        if cast.media_controller.status.player_state in ("PLAYING", "BUFFERING"):
            cast.media_controller.pause()
            time.sleep(1)


@requires_enabled(None)
def play(device, stream_url, title):
    with _cast(device) as cast:
        cast.media_controller.play_media(stream_url, "audio/mp3", title=title)
        cast.media_controller.block_until_active(timeout=10)


@requires_enabled(None)
def resume(device):
    with _cast(device) as cast:
        cast.media_controller.update_status()
        time.sleep(1)
        if cast.media_controller.status.player_state == "PAUSED":
            cast.media_controller.play()
            time.sleep(1)


@requires_enabled(None)
def stop(device):
    with _cast(device) as cast:
        cast.quit_app()
        time.sleep(1)


@requires_enabled(None)
def set_volume(device, lvl):
    with _cast(device) as cast:
        cast.set_volume(lvl / 100)
        time.sleep(1)


@contextmanager
def _cast(device, **kwargs):
    ip = socket.gethostbyname(device.value)
    cast = pychromecast.get_chromecast_from_host((ip, 8009, None, None, None), **kwargs)
    try:
        cast.wait(timeout=kwargs.get("timeout"))
        yield cast
    finally:
        cast.disconnect(timeout=2)


def _strip_googlevideo_params(url):
    parsed = urlparse(url)
    if not parsed.hostname or not parsed.hostname.endswith("googlevideo.com"):
        return url
    vid_id = parse_qs(parsed.query).get("id", [None])[0]
    query = urlencode({"id": vid_id}) if vid_id is not None else ""
    return urlunparse(parsed._replace(query=query))
