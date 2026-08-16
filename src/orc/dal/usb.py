import array
import audioop
import wave
from collections.abc import Iterable
from functools import lru_cache
from importlib import resources  # nosemgrep
from importlib.resources.abc import Traversable  # nosemgrep
from typing import Any

import pyaudio

from orc import config
from orc import model as m
from orc.decorators import audio_lock, silence_fd

_MODEL_PATH: Traversable = resources.files("orc_data") / "en_GB-alba-medium.onnx"
_CONFIG_PATH: Traversable = resources.files("orc_data") / "en_GB-alba-medium.onnx.json"
with silence_fd(2):
    from piper import PiperVoice

    # resources.files() yields a concrete Path here; piper's stub only accepts str | Path, not the broader Traversable
    _VOICE: Any = PiperVoice.load(_MODEL_PATH, _CONFIG_PATH, use_cuda=False)  # type: ignore[arg-type]


def play_alert(path: str, level: str | None = None) -> None:
    with wave.open(path, "rb") as wf:
        channels, rate = wf.getnchannels(), wf.getframerate()
        chunks = iter(lambda: wf.readframes(4096), b"")
        _play_stream(chunks, channels, rate, _gain_for(level))


def play_text(text: str, level: str | None = None) -> None:
    chunks = (a.audio_int16_bytes for a in _VOICE.synthesize(text))
    _play_stream(chunks, 1, _VOICE.config.sample_rate, _gain_for(level))


def _scale_int16(frames: bytes, gain: float) -> bytes:
    if gain == 1.0:
        return frames
    samples = array.array("h", frames)
    for i, s in enumerate(samples):
        v = int(s * gain)
        samples[i] = -32768 if v < -32768 else 32767 if v > 32767 else v
    return samples.tobytes()


@lru_cache(maxsize=1)
def _find_output_device(name: str) -> tuple[int, Any]:
    with silence_fd(2):
        pa = pyaudio.PyAudio()
    try:
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if name in info["name"] and info["maxOutputChannels"] > 0:
                return i, info
    finally:
        pa.terminate()
    raise RuntimeError(f"No audio output device matching audio_device setting {name!r}")


def _play_stream(chunks: Iterable[bytes], channels: int, src_rate: int, gain: float) -> None:
    idx, info = _find_output_device(config.settings.audio_device)
    dst_rate = int(info["defaultSampleRate"])
    with audio_lock, silence_fd(2):
        pa = pyaudio.PyAudio()
        try:
            stream = pa.open(format=pyaudio.paInt16, channels=channels, rate=dst_rate, output_device_index=idx, output=True)
            try:
                state = None
                for chunk in chunks:
                    scaled = _scale_int16(chunk, gain)
                    if src_rate != dst_rate:
                        scaled, state = audioop.ratecv(scaled, 2, channels, src_rate, dst_rate, state)
                    stream.write(scaled)
            finally:
                stream.stop_stream()
                stream.close()
        finally:
            pa.terminate()


def _gain_for(level: str | None) -> float:
    volume = config.volumes.FATAL if level == m.AUDIO_FATAL else config.volumes.INFO
    return volume / 100.0
