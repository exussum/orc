import itertools
import time
from collections import namedtuple as nt
from dataclasses import replace
from datetime import datetime, timedelta
from enum import Enum
from importlib import resources

import numpy as np
import pygame
import sounddevice as sd
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from astral import Observer, sun
from piper import PiperVoice

from orc import config, dal
from orc import model as m

SnapShot = nt("SnapShot", "routine end")
ThemeOverride = nt("ThemeOverride", "name start end")

_ACTIVITY_LOG = m.ActivityLog()

_MODEL_PATH = resources.files("orc.pkg") / "en_GB-alba-medium.onnx"
_CONFIG_PATH = resources.files("orc.pkg") / "en_GB-alba-medium.onnx.json"
_VOICE = PiperVoice.load(_MODEL_PATH, _CONFIG_PATH, use_cuda=False)


def log(when, source, action):
    _ACTIVITY_LOG.add(when, source, action)


def log_entries():
    return _ACTIVITY_LOG.entries


def local_now():
    return datetime.now(tz=config.TZ)


def jobs_by_type(scheduler, type):
    now = local_now()
    return [e for e in scheduler.get_jobs() if isinstance(e.func, type) and e.trigger.run_date > now]


def unwrap_rule_container(f):
    def wrapper(*args):
        if isinstance(args[0], m.Routine | m.Configs):
            for e in args[0].items:
                f(*((e,) + args[1:]))
        elif len(args) > 1 and isinstance(args[1], m.Routine | m.Configs):
            for e in args[1].items:
                f(
                    *(
                        (
                            args[0],
                            e,
                        )
                        + args[2:]
                    )
                )
        else:
            f(*args)

    return wrapper


class ConfigManager:
    def __init__(self):
        self.snapshot = None
        self.theme_override = None

    def replace_config(self, target_config, end):

        if not self.snapshot:
            self.snapshot = SnapShot(capture_lights(), end)

        execute(target_config)

    def resume(self, target_config):
        if self.snapshot and local_now() <= self.snapshot.end:
            routine = self.snapshot.routine
        else:
            routine = target_config
        self.snapshot = None
        execute(routine)

    def update_snapshot(self, rule):
        what = [rule.what] if isinstance(rule.what, Enum) else rule.what
        items = {e.what: e for e in self.snapshot.routine.items}

        # Explode out the rule w/o creating a sub config explicitly
        items.update({e: replace(rule, what=e) for e in what})
        self.snapshot = self.snapshot._replace(routine=m.Configs(*items.values()))

    def set_theme_override(self, name, start, end):
        self.theme_override = ThemeOverride(name, start, end)

    def calculate_theme(self, today):
        if self.theme_override and self.theme_override.start <= today <= self.theme_override.end:
            return self.theme_override.name

        if today.weekday() not in (5, 6):
            today_iso = today.strftime("%Y-%m-%d")
            market_schedule = dal.get_holidays(today.year)
            theme_name = (
                "day off" if next((e for e in market_schedule if e["date"] == today_iso and e["exchange"] == "NYSE"), None) else "work day"
            )
        else:
            theme_name = "day off"
        return theme_name

    @unwrap_rule_container
    def route_rule(self, rule, force):
        if rule.mandatory and self.snapshot:
            self.update_snapshot(rule)
            execute(rule)
        elif self.snapshot and local_now() > self.snapshot.end:
            self.snapshot = None
            execute(rule)
        elif not self.snapshot or force:
            execute(rule)


@unwrap_rule_container
def execute(rule):
    what = [rule.what] if isinstance(rule.what, Enum) else rule.what
    sleep = time.sleep if len(what) > 1 else (lambda _: 1)
    for w in what:
        if isinstance(w, config.Light):
            (dal.set_light(w, brightness=rule.state) if isinstance(rule.state, int) else dal.set_light(w, on=rule.state == "on"))
        elif isinstance(w, config.Sound):
            if isinstance(rule.state, int):
                dal.set_sound(w, rule.state)
            elif rule.state == "stop":
                dal.stop_sound(w)
            else:
                dal.play_youtube(w, rule.state)
        else:
            raise Exception("Unknown type")
        sleep(0.1)


def capture_lights():
    return m.Configs(*(dal.get_light_state(e) for e in config.Light))


def get_schedule(config_manager):
    result = []
    observer = Observer(*config.LAT_LONG)
    for x in range(2):
        now = local_now() + timedelta(days=x)
        today = now.date()
        sunrise = sun.sunrise(observer, today) + timedelta(minutes=30)
        sunset = sun.sunset(observer, today) - timedelta(minutes=30)

        cfg = config.THEMES.get(today.strftime("%A").lower()) or config.THEMES.get(config_manager.calculate_theme(today))

        for e in cfg.configs:
            if e.when == "sunrise":
                time = sunrise
            elif e.when == "sunset":
                time = sunset
            else:
                time = now.replace(hour=e.when.hour, minute=e.when.minute, second=0)
            result.append((time.astimezone(config.TZ), e))
    return result


def _make_rule_lambda(config_manager, rule):
    def f(force):
        if not force:
            log(local_now(), m.LogSource.SCHEDULED, rule.name)
        config_manager.route_rule(rule, force)

    return f


def _make_lambda(f, *args, **kwargs):
    return lambda: f(*args, **kwargs)


def setup_iot_scheduler(scheduler, config_manager):
    def f():
        now = local_now()
        for time, rule in get_schedule(config_manager):
            if now <= time:
                scheduler.add_job(
                    m.IotJob(_make_rule_lambda(config_manager, rule)),
                    DateTrigger(time),
                    name=rule.name,
                    id=f"iot-{rule.name}-{time.date().isoformat()}",
                    replace_existing=True,
                )

    f()
    scheduler.add_job(f, CronTrigger.from_crontab("10 0 * * *"), replace_existing=True, name="Iot Cron")
    return scheduler


def setup_cal_scheduler(scheduler, config_manager, sound_path):
    def f():
        schedule_cal_tasks(scheduler, config_manager, sound_path)

    scheduler.add_job(f, CronTrigger.from_crontab("*/5 8-18 * * *"), name="Calendar Cron")
    return scheduler


def schedule_cal_tasks(scheduler, config_manager, sound_path):
    now = local_now()
    if config_manager.calculate_theme(now.date()) == "work day" and (now.time().minute in [55, 10, 25, 40]):
        events = list(itertools.islice(dal.read_ical(now, timedelta(hours=20)), 50))
        warning_events = (m.CalendarEvent.from_cal(e, "warning", timedelta(minutes=-2), config.TZ) for e in events)
        alarm_events = (m.CalendarEvent.from_cal(e, "alarm", timedelta(), config.TZ) for e in events)

        calendar_by_id = {e.uuid: e for e in itertools.chain.from_iterable((alarm_events, warning_events))}

        for e in jobs_by_type(scheduler, m.CalendarJob):
            if e.id not in calendar_by_id:
                scheduler.remove_job(e.id)

        for id, event in calendar_by_id.items():
            play_sound = _make_lambda(play_alert, sound_path) if event.type == "warning" else _make_lambda(play_text, event.summary)
            scheduler.add_job(
                m.CalendarJob(play_sound),
                DateTrigger(event.datetime),
                replace_existing=True,
                id=id,
                name=event.summary,
            )


def play_text(text):
    with sd.OutputStream(samplerate=_VOICE.config.sample_rate, channels=1, dtype="int16") as stream:
        for audio_bytes in _VOICE.synthesize(text):
            stream.write(np.frombuffer(audio_bytes.audio_int16_bytes, dtype=np.int16))
        stream.write(np.frombuffer(b"\x00" * 10000, dtype=np.int16))


def play_alert(path):
    pygame.mixer.init()
    sound = pygame.mixer.Sound(path)
    playing = sound.play()
    while playing.get_busy():
        pygame.time.delay(100)


def test(theme):
    time.sleep(1)
    for e in theme.items:
        execute(e)
        time.sleep(2)
