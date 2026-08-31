import wave
from collections.abc import Iterable
from functools import cache
from importlib import resources  # nosemgrep
from importlib.resources.abc import Traversable  # nosemgrep
from typing import Any

import audioop
import pyaudio

from orc import model as m
from orc.dal import system_volume
from orc.decorators import audio_lock, silence_fd

_MODEL_PATH: Traversable = resources.files("orc_data") / "en_GB-alba-medium.onnx"
_CONFIG_PATH: Traversable = resources.files("orc_data") / "en_GB-alba-medium.onnx.json"
with silence_fd(2):
    from piper import PiperVoice

    # resources.files() yields a concrete Path here; piper's stub only accepts str | Path, not the broader Traversable
    _VOICE: Any = PiperVoice.load(_MODEL_PATH, _CONFIG_PATH, use_cuda=False)  # type: ignore[arg-type]


def alert(device: m.DeviceEnum, path: str) -> None:
    with wave.open(path, "rb") as wf:
        channels, rate = wf.getnchannels(), wf.getframerate()
        chunks = iter(lambda: wf.readframes(4096), b"")
        _play_stream(device, chunks, channels, rate)


def speak(device: m.DeviceEnum, text: str) -> None:
    chunks = (a.audio_int16_bytes for a in _VOICE.synthesize(text))
    _play_stream(device, chunks, 1, _VOICE.config.sample_rate)


def set_volume(device: m.DeviceEnum, lvl: int) -> None:
    system_volume.set_volume(device.value, lvl)


def fetch_state(device: m.DeviceEnum) -> m.SoundState:
    return m.SoundState(what=device, content=None, volume=system_volume.get_volume(device.value))


@cache
def _find_output_device(serial: str) -> tuple[int, Any]:
    card_idx = system_volume.card_index_for_serial(serial)
    marker = f"(hw:{card_idx},"
    with silence_fd(2):
        pa = pyaudio.PyAudio()
    try:
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if marker in info["name"] and info["maxOutputChannels"] > 0:
                return i, info
    finally:
        pa.terminate()
    raise RuntimeError(f"No audio output device for ALSA card {card_idx} (serial {serial!r})")


def list_devices_cli() -> None:
    with silence_fd(2):
        pa = pyaudio.PyAudio()
    try:
        infos = [pa.get_device_info_by_index(i) for i in range(pa.get_device_count())]
    finally:
        pa.terminate()
    pa_names = [info["name"] for info in infos if info["maxOutputChannels"] > 0]

    for dev in system_volume.list_usb_audio_devices():
        marker = f"(hw:{dev.card_index},"
        pa_name = next((name for name in pa_names if marker in name), "?")
        print(f"card {dev.card_index:<2} serial={dev.serial or '(none)':<20} alsa={dev.card:<15} portaudio={pa_name}")


def _play_stream(device: m.DeviceEnum, chunks: Iterable[bytes], channels: int, src_rate: int) -> None:
    idx, info = _find_output_device(device.value)
    dst_rate = int(info["defaultSampleRate"])
    with audio_lock, silence_fd(2):
        pa = pyaudio.PyAudio()
        try:
            stream = pa.open(format=pyaudio.paInt16, channels=channels, rate=dst_rate, output_device_index=idx, output=True)
            try:
                state = None
                for chunk in chunks:
                    if src_rate != dst_rate:
                        chunk, state = audioop.ratecv(chunk, 2, channels, src_rate, dst_rate, state)
                    stream.write(chunk)
            finally:
                stream.stop_stream()
                stream.close()
        finally:
            pa.terminate()
