import time
from dataclasses import replace
from datetime import datetime, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

import requests
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from orc import config


def set_light(light, on=None, brightness=None):
    if brightness:
        requests.get(f"{config.BASE_URL}/devices/{light.value}/setLevel/{brightness}{config.ACCESS_TOKEN}")
    else:
        requests.get(f"{config.BASE_URL}/devices/{light.value}/{'on' if on else 'off'}{config.ACCESS_TOKEN}").content


def cast_initialize(sound):
    requests.get(f"{config.BASE_URL}/devices/{sound.value}/initialize{config.ACCESS_TOKEN}").json()


def set_sound(sound, lvl):
    requests.get(f"{config.BASE_URL}/devices/{sound.value}/setVolume/{lvl}{config.ACCESS_TOKEN}").json()


def calculate_theme(today):
    if today.day not in (0, 6) or True:
        today_iso = today.strftime("%Y-%m-%d")
        market_schedule = requests.get(f"{config.MARKET_HOLIDAYS_URL}").json()
        theme_name = (
            "holiday"
            if next((e for e in market_schedule if e["date"] == today_iso and e["exchange"] == "NYSE"), None)
            else "non-holiday"
        )
    else:
        theme_name = "holiday"
    return theme_name


def build_schedule():
    now = datetime.now(tz=ZoneInfo("America/New_York"))
    sun_result = requests.get(f"{config.SUNRISE_URL}&date={now.date()}").json()["results"]
    sunrise = datetime.fromisoformat(sun_result["sunrise"])
    sunset = datetime.fromisoformat(sun_result["sunset"])

    theme = calculate_theme(now.date())
    cfg = next((e for e in config.CONFIGS if e.days == theme))

    result = []
    for e in cfg.configs:
        if e.when == "sunrise":
            time = sunrise
        elif e.when == "sunset":
            time = sunset
        else:
            time = now.replace(hour=e.when.hour, minute=e.when.minute, second=0)
        time = time + e.offset

        result.append((time, e))

    return result


def execute(rule):
    if isinstance(rule, config.RoutineConfig):
        for e in rule.items:
            execute(e)
    else:
        what = [rule.what] if isinstance(rule.what, Enum) else rule.what
        sleep = time.sleep if len(what) > 1 else (lambda _: 1)
        for w in what:
            if w.value < 0:
                print(f"Device {w} not found")
            else:
                if isinstance(rule, config.LightConfig):
                    (
                        set_light(w, brightness=rule.state)
                        if isinstance(rule.state, int)
                        else set_light(w, on=rule.state == "on")
                    )
                else:
                    (cast_initialize(w) if rule.state == "initialize" else set_sound(w, rule.state))
                sleep(1)


def setup_scheduler(scheduler):
    def f():
        for time, rule in build_schedule():
            scheduler.add_job(lambda: execute(rule), DateTrigger(time))

    f()
    scheduler.add_job(f, CronTrigger.from_crontab("30 0 * * *"))
    return scheduler
