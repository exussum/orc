import io
import subprocess
import tempfile
import urllib.request
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode

from PIL import Image, ImageDraw, ImageFont, ImageText

from orc.dal.chromecast import MAX_CHARS

ALERT_IMAGE_SIZE = (1280, 720)

_ALERT_MARGIN = 80
_ALERT_MIN_FONT_SIZE = 24
_ALERT_VIDEO_SECONDS = 300
_ALERT_LOOP_SECONDS = 20
_TTS_SAMPLE_RATE = 24000
_TTS_TIMEOUT = 10


@lru_cache(maxsize=5)
def render_alert_video(text: str) -> bytes:
    if len(text) > MAX_CHARS:
        raise ValueError(f"Alert text exceeds {MAX_CHARS} characters: {len(text)}")
    with tempfile.TemporaryDirectory() as d:
        png, mp3 = Path(d) / "a.png", Path(d) / "a.mp3"
        seg, mp4 = Path(d) / "seg.mp4", Path(d) / "a.mp4"
        png.write_bytes(_render_alert_image(text))
        mp3.write_bytes(_tts_mp3(text))
        audio = f"[1:a]aresample={_TTS_SAMPLE_RATE},apad=whole_dur={_ALERT_LOOP_SECONDS}[a]"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error"]
            + ["-loop", "1", "-i", str(png), "-i", str(mp3)]
            + ["-filter_complex", audio, "-map", "0:v", "-map", "[a]"]
            + ["-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage", "-pix_fmt", "yuv420p", "-r", "1"]
            + ["-c:a", "aac", "-b:a", "64k", "-t", str(_ALERT_LOOP_SECONDS), str(seg)],
            check=True,
        )
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", str(seg)]
            + ["-c", "copy", "-t", str(_ALERT_VIDEO_SECONDS), "-movflags", "+faststart", str(mp4)],
            check=True,
        )
        return mp4.read_bytes()


def _render_alert_image(text: str) -> bytes:
    image = Image.new("RGB", ALERT_IMAGE_SIZE, color=(178, 24, 24))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=128)
    width, height = ALERT_IMAGE_SIZE[0] - 2 * _ALERT_MARGIN, ALERT_IMAGE_SIZE[1] - 2 * _ALERT_MARGIN
    wrapped = ImageText.Text(text, font=font)
    wrapped.wrap(width, height, scaling=("shrink", _ALERT_MIN_FONT_SIZE))
    if "\n" not in wrapped.text:
        # scaling wrap early-returns without writing the wrapped lines back when the
        # text fits at the starting size. Drop this once the early return in the
        # scaling=="shrink" branch of ImageText.Text.wrap is fixed upstream:
        # https://github.com/python-pillow/Pillow/pull/9286 (present through 12.3.0).
        wrapped.wrap(width, height)
    draw.text((ALERT_IMAGE_SIZE[0] / 2, ALERT_IMAGE_SIZE[1] / 2), wrapped, fill="white", anchor="mm", align="center")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _tts_mp3(text: str) -> bytes:
    url = "https://translate.google.com/translate_tts?" + urlencode({"ie": "UTF-8", "q": text, "tl": "en", "client": "tw-ob"})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=_TTS_TIMEOUT) as resp:  # nosemgrep
        return resp.read()
