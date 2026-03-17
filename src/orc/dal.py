import time
from functools import lru_cache
from importlib import resources

import icalendar
import numpy as np
import pygame
import recurring_ical_events
import requests
import sounddevice as sd
from piper import PiperVoice

from orc import config
from orc import model as m


def get_light_state(light):
    resp = requests.get(f"{config.BASE_URL}/devices/{light.value}{config.ACCESS_TOKEN}")
    if resp.status_code == 200:
        attrs = {e["name"]: e["currentValue"] for e in resp.json()["attributes"]}
        return m.Config(what=light, state=attrs["level"] if ("level" in attrs and attrs["switch"] == "on") else attrs["switch"])
    else:
        return m.Config(what=light, state=config.OFF)


def set_light(light, on=None, brightness=None):
    if brightness:
        requests.get(f"{config.BASE_URL}/devices/{light.value}/setLevel/{brightness}{config.ACCESS_TOKEN}")
    else:
        requests.get(f"{config.BASE_URL}/devices/{light.value}/{'on' if on else 'off'}{config.ACCESS_TOKEN}").content


def set_sound(sound, lvl):
    requests.get(f"{config.BASE_URL}/devices/{sound.value}/speak/%20{config.ACCESS_TOKEN}").json()
    time.sleep(1)
    requests.get(f"{config.BASE_URL}/devices/{sound.value}/setVolume/{lvl}{config.ACCESS_TOKEN}").json()


def play_alert(path):
    pygame.mixer.init()
    sound = pygame.mixer.Sound(path)
    playing = sound.play()
    while playing.get_busy():
        pygame.time.delay(100)


def play_text(text):
    model_path = resources.files("orc.pkg") / "en_GB-alba-medium.onnx"
    config_path = resources.files("orc.pkg") / "en_GB-alba-medium.onnx.json"
    voice = PiperVoice.load(model_path, config_path)

    with sd.OutputStream(samplerate=voice.config.sample_rate, channels=1, dtype="int16") as stream:
        for audio_bytes in voice.synthesize(text):
            stream.write(np.frombuffer(audio_bytes.audio_int16_bytes, dtype=np.int16))
        stream.write(np.frombuffer(b"\x00" * 10000, dtype=np.int16))


def get_config():
    return requests.get(f"{config.BASE_URL}/devices{config.ACCESS_TOKEN}").json()


@lru_cache(maxsize=2)
def get_holidays(year):
    result = requests.get(f"{config.MARKET_HOLIDAYS_URL}").json()
    if "error" in result:
        print(result["error"])
        return []
    return result


def read_ical(start, end):
    ical_string = requests.get(config.ICS_URL).content
    a_calendar = icalendar.Calendar.from_ical(ical_string)
    # between leaks out events that have already started
    return (e for e in recurring_ical_events.of(a_calendar).between(start, end) if e.start >= start)
