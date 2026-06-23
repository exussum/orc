import array
import audioop
import wave
from functools import lru_cache
from importlib import resources

import pyaudio

from orc import config
from orc._decorators import audio_lock, silence_fd

_MODEL_PATH = resources.files("orc_data") / "en_GB-alba-medium.onnx"
_CONFIG_PATH = resources.files("orc_data") / "en_GB-alba-medium.onnx.json"
with silence_fd(2):
    from piper import PiperVoice

    _VOICE = PiperVoice.load(_MODEL_PATH, _CONFIG_PATH, use_cuda=False)


def _scale_int16(frames, gain):
    if gain == 1.0:
        return frames
    samples = array.array("h", frames)
    for i, s in enumerate(samples):
        v = int(s * gain)
        samples[i] = -32768 if v < -32768 else 32767 if v > 32767 else v
    return samples.tobytes()


@lru_cache(maxsize=1)
def _find_output_device(name):
    with silence_fd(2):
        pa = pyaudio.PyAudio()
    try:
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if name in info["name"] and info["maxOutputChannels"] > 0:
                return i, info
    finally:
        pa.terminate()
    raise RuntimeError(f"No audio output device matching ORC_AUDIO_DEVICE={name!r}")


def _play_stream(chunks, channels, src_rate, gain):
    idx, info = _find_output_device(config.audio_device)
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


def _gain_for(level):
    return config.audio_volumes[level or config.AUDIO_INFO] / 100.0


def play_alert(path, level=None):
    with wave.open(path, "rb") as wf:
        channels, rate = wf.getnchannels(), wf.getframerate()
        chunks = iter(lambda: wf.readframes(4096), b"")
        _play_stream(chunks, channels, rate, _gain_for(level))


def play_text(text, level=None):
    chunks = (a.audio_int16_bytes for a in _VOICE.synthesize(text))
    _play_stream(chunks, 1, _VOICE.config.sample_rate, _gain_for(level))
