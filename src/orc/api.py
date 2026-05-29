import copy
import io
import itertools
import time
import wave
from collections import namedtuple as nt
from concurrent.futures import ThreadPoolExecutor as Pool
from dataclasses import replace
from datetime import datetime, timedelta
from enum import Enum
from importlib import resources

import pygame
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from piper import PiperVoice
from skyfield import almanac
from skyfield.api import load, load_file, wgs84

import orc
from orc import config, dal
from orc import model as m
from orc.dal import (  # noqa: F401
    get_chromecast_config,
    get_hubitat_config,
    get_secrets,
    init_db,
)


class ContextThreadPoolExecutor(ThreadPoolExecutor):
    def __init__(self, ctx: m.AppContext, max_workers=1):
        super().__init__(max_workers=max_workers)
        self.ctx = ctx

    def _do_submit_job(self, job, run_times):
        dispatch_job = copy.copy(job)
        dispatch_job._jobstore_alias = job._jobstore_alias
        dispatch_job.kwargs = {**job.kwargs, "ctx": self.ctx}
        return super()._do_submit_job(dispatch_job, run_times)

    def run_now(self, job, **extra_kwargs):
        return job.func(*job.args, ctx=self.ctx, **{**job.kwargs, **extra_kwargs})


SnapShot = nt("SnapShot", "routine end")
ThemeOverride = nt("ThemeOverride", "name start end")
PRESENCE_WINDOW = timedelta(hours=12)

_ACTIVITY_LOG = m.ActivityLog()

_MODEL_PATH = resources.files("orc_data") / "en_GB-alba-medium.onnx"
_CONFIG_PATH = resources.files("orc_data") / "en_GB-alba-medium.onnx.json"
_VOICE = PiperVoice.load(_MODEL_PATH, _CONFIG_PATH, use_cuda=False)

_EPHEMERIS_PATH = resources.files("orc_data") / "de421.bsp"
_TIMESCALE = load.timescale()
_EPHEMERIS = load_file(str(_EPHEMERIS_PATH))
_TWILIGHT_FN = almanac.dark_twilight_day(_EPHEMERIS, wgs84.latlon(*config.lat_long))


def log(when, source, action):
    _ACTIVITY_LOG.add(when, source, action)


def log_entries():
    return _ACTIVITY_LOG.entries


def local_now():
    return datetime.now(tz=config.tz)


def jobs_by_type(scheduler, type):
    now = local_now()
    return [e for e in scheduler.get_jobs() if e.args and isinstance(e.args[0], type) and e.trigger.run_date > now]


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
        row = dal.load_theme_override()
        self._theme_override = ThemeOverride(*row) if row else None

    @property
    def theme_override(self):
        if self._theme_override and self._theme_override.end < local_now().date():
            return None
        return self._theme_override

    @theme_override.setter
    def theme_override(self, value):
        self._theme_override = value
        dal.save_theme_override(value)

    def replace_config(self, target_config, end):

        if not self.snapshot:
            self.snapshot = SnapShot(capture_lights(), end)
            items = ", ".join(f"{c.what.name}={c.state}" for c in self.snapshot.routine.items)
            log(local_now(), m.LogSource.SYSTEM, f"Snapshot until {end:%H:%M}: {items}")

        execute(target_config)

    def resume(self, target_config):
        if self.snapshot and local_now() <= self.snapshot.end:
            routine = self.snapshot.routine
            log(local_now(), m.LogSource.SYSTEM, "Snapshot restored")
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

    def presence(self):
        return dal.load_presence()

    @property
    def present_names(self):
        cutoff = local_now() - PRESENCE_WINDOW
        return {name for name, ts in self.presence().items() if ts >= cutoff}

    def mark_present(self, names):
        now = local_now()
        for name in names:
            dal.save_presence(name, now)

    def expire_presence(self, name):
        dal.delete_presence(name)

    def active_override(self, today):
        if self.theme_override and self.theme_override.start <= today <= self.theme_override.end:
            return self.theme_override
        return None

    def calculate_theme(self, today):
        if override := self.active_override(today):
            return override.name

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
        if rule.trigger == m.Trigger.SYSTEM and self.snapshot:
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
    stream = {}
    for w in what:
        if w in config.virtual_devices:
            print("Skipping virtual device:" + w)
            continue

        if isinstance(w, orc.Light):
            if isinstance(rule.state, int):
                dal.set_light(w, brightness=rule.state)
            else:
                dal.set_light(w, on=rule.state == "on")
        elif isinstance(w, orc.Sound):
            if isinstance(rule.state, int):
                dal.set_sound(w, rule.state)
            elif rule.state == "stop":
                dal.stop_sound(w)
            else:
                if rule.state not in stream:
                    if "http" in rule.state:
                        stream[rule.state] = (rule.state, rule.state)
                    else:
                        stream[rule.state] = dal.resolve_youtube(rule.state)
                dal.play_stream(w, *stream[rule.state])
        else:
            raise Exception("Unknown type")
        sleep(0.1)


def capture_lights():
    return m.Configs(*(dal.get_light_state(e) for e in orc.Light))


def capture_sounds():
    return m.Configs(*dal.get_sound(orc.Sound))


def get_schedule(config_manager):
    result = []
    for x in range(2):
        now = local_now() + timedelta(days=x)
        today = now.date()
        local_midnight = datetime(today.year, today.month, today.day, tzinfo=config.tz)
        day_start = _TIMESCALE.from_datetime(local_midnight)
        day_end = _TIMESCALE.from_datetime(local_midnight + timedelta(days=1))
        times, twilight = almanac.find_discrete(day_start, day_end, _TWILIGHT_FN)
        sunrise = sunset = None
        prev = int(_TWILIGHT_FN(day_start).item())
        for t, curr in zip(times, twilight):
            curr = int(curr)
            if (prev, curr) == (3, 4):
                sunrise = t.utc_datetime()
            elif (prev, curr) == (4, 3):
                sunset = t.utc_datetime() - timedelta(hours=1)
            prev = curr

        if override := config_manager.active_override(today):
            cfg = config.themes.get(override.name)
        else:
            cfg = config.themes.get(today.strftime("%A").lower()) or config.themes.get(config_manager.calculate_theme(today))

        for e in cfg.configs:
            if e.when == "sunrise":
                time = sunrise
            elif e.when == "sunset":
                time = sunset
            else:
                time = now.replace(hour=e.when.hour, minute=e.when.minute, second=0)
            result.append((time.astimezone(config.tz), e))
    return result


def should_skip_for_presence(rule, force, present_names):
    if force or not rule.items:
        return False
    for c in rule.items:
        if not c.trigger or c.trigger == m.Trigger.SYSTEM or c.trigger in present_names:
            return False
        if c.trigger == m.Trigger.ANYONE and present_names:
            return False
    return True


def run_iot_job(job, ctx=None, force=False):
    if ctx is None:
        raise ValueError("ctx must be injected by the executor")
    rule = job.rule
    if should_skip_for_presence(rule, force, ctx.config_manager.present_names):
        absent = sorted({
            c.trigger for c in rule.items
            if c.trigger not in (None, m.Trigger.SYSTEM, m.Trigger.ANYONE)
        })
        detail = f"absent: {', '.join(absent)}" if absent else "no one present"
        log(local_now(), m.LogSource.IOT, f"Skipped {rule.name} ({detail})")
        return
    if not force:
        log(local_now(), m.LogSource.IOT, rule.name)
    ctx.config_manager.route_rule(rule, force)


def run_cal_job(job, ctx=None):
    if ctx is None:
        raise ValueError("ctx must be injected by the executor")
    if job.event_type == "warning":
        play_alert(ctx.sound_path)
    else:
        log(local_now(), m.LogSource.CALENDAR, job.summary)
        play_text(job.summary)


def _safe_ping(name, host):
    try:
        return name, dal.ping_host(host)
    except Exception as exc:
        log(local_now(), m.LogSource.SYSTEM, f"Presence ping failed for {name}: {exc}")
        return name, False


def check_presence(ctx=None):
    if ctx is None:
        raise ValueError("ctx must be injected by the executor")
    pairs = [(name, host) for name, hosts in config.people.items() for host in hosts]
    if not pairs:
        return
    before = ctx.config_manager.present_names
    with Pool(max_workers=len(pairs)) as ex:
        present = {name for name, ok in ex.map(lambda nh: _safe_ping(*nh), pairs) if ok}
    ctx.config_manager.mark_present(present)
    after = ctx.config_manager.present_names
    for name in sorted(after - before):
        log(local_now(), m.LogSource.SYSTEM, f"Presence detected: {name}")
    for name in sorted(before - after):
        log(local_now(), m.LogSource.SYSTEM, f"Presence lost: {name}")


def rebuild_iot_schedule(ctx=None):
    if ctx is None:
        raise ValueError("ctx must be injected by the executor")
    now = local_now()
    for time, rule in get_schedule(ctx.config_manager):
        if now <= time:
            ctx.scheduler.add_job(
                run_iot_job,
                DateTrigger(time),
                args=[m.IotJob(rule)],
                name=rule.name,
                id=f"iot-{rule.name}-{time.date().isoformat()}",
                replace_existing=True,
            )


def rebuild_cal_schedule(ctx=None):
    if ctx is None:
        raise ValueError("ctx must be injected by the executor")
    schedule_cal_tasks(ctx.scheduler, ctx.config_manager)


def setup_scheduler(ctx):
    if not jobs_by_type(ctx.scheduler, m.IotJob):
        rebuild_iot_schedule(ctx)
    ctx.scheduler.add_job(
        rebuild_iot_schedule,
        CronTrigger.from_crontab("10 0 * * *"),
        replace_existing=True,
        id="iot-cron",
        name="Iot Cron",
        jobstore="memory",
    )
    ctx.scheduler.add_job(
        rebuild_cal_schedule,
        CronTrigger.from_crontab("*/5 8-18 * * *"),
        replace_existing=True,
        id="cal-cron",
        name="Calendar Cron",
        jobstore="memory",
    )
    ctx.scheduler.add_job(
        check_presence,
        CronTrigger.from_crontab("0 * * * *"),
        replace_existing=True,
        id="presence-cron",
        name="Presence Cron",
        jobstore="memory",
    )


def schedule_cal_tasks(scheduler, config_manager):
    now = local_now()
    if config_manager.calculate_theme(now.date()) == "work day" and (now.time().minute in [55, 10, 25, 40]):
        events = list(itertools.islice(dal.read_ical(now, timedelta(hours=20)), 50))
        warning_events = (m.CalendarEvent.from_cal(e, "warning", timedelta(minutes=-2), config.tz) for e in events)
        alarm_events = (m.CalendarEvent.from_cal(e, "alarm", timedelta(), config.tz) for e in events)

        calendar_by_id = {e.uuid: e for e in itertools.chain.from_iterable((alarm_events, warning_events))}

        for e in jobs_by_type(scheduler, m.CalendarJob):
            if e.id not in calendar_by_id:
                scheduler.remove_job(e.id)

        for id, event in calendar_by_id.items():
            scheduler.add_job(
                run_cal_job,
                DateTrigger(event.datetime),
                args=[m.CalendarJob(event.type, event.summary)],
                replace_existing=True,
                id=id,
                name=event.summary,
                jobstore="memory",
            )


def play_text(text):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(_VOICE.config.sample_rate)
        for audio_bytes in _VOICE.synthesize(text):
            fh.writeframes(audio_bytes.audio_int16_bytes)
    buf.seek(0)

    pygame.mixer.init()
    playing = pygame.mixer.Sound(buf).play()
    while playing.get_busy():
        pygame.time.delay(100)


def play_alert(path):
    pygame.mixer.init()
    sound = pygame.mixer.Sound(path)
    playing = sound.play()
    while playing.get_busy():
        pygame.time.delay(100)


def sound_test(theme, sound_path):
    time.sleep(1)
    for e in theme.items:
        execute(e)
        time.sleep(5)
    execute(m.Config(orc.Sound, "stop"))
    play_alert(sound_path)
    play_text("audio test")


def light_test():
    execute(m.Config(orc.Light, config.ON))
    time.sleep(10)


def replay_day(config_manager, now):
    jobs = sorted(get_schedule(config_manager), key=lambda x: x[0])
    configs = (cfg for (when, cfg) in jobs if when <= now)
    execute(m.squish_configs(*configs))
