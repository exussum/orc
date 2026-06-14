import io
import itertools
import os
import sys
import time
import wave
from collections import namedtuple as nt
from concurrent.futures import ThreadPoolExecutor as Pool
from dataclasses import replace
from datetime import datetime, timedelta
from enum import Enum
from importlib import resources

import pygame
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from piper import PiperVoice
from skyfield import almanac
from skyfield.api import load, load_file, wgs84

import orc
from orc import config
from orc import model as m
from orc.apscheduler import JOBSTORE_MEMORY, requires_ctx
from orc.dal import chromecast, discovery, feeds, lights, sqlite, tv
from orc.dal.bws import fetch_secrets  # noqa: F401
from orc.dal.chromecast import pause, resume, stop  # noqa: F401
from orc.dal.discovery import fetch_hubitat_config  # noqa: F401
from orc.dal.lgtv import pair as pair_lg_tv  # noqa: F401
from orc.dal.sqlite import init_db  # noqa: F401
from orc.dal.tv import fetch_macs  # noqa: F401
from orc.locale import Log

_PRESENCE_WINDOW = timedelta(hours=9)
_ACTIVITY_LOG = m.ActivityLog()
_CALENDAR_CHECK_MINUTES = (55, 10, 25, 40)

_MODEL_PATH = resources.files("orc_data") / "en_GB-alba-medium.onnx"
_CONFIG_PATH = resources.files("orc_data") / "en_GB-alba-medium.onnx.json"
_VOICE = PiperVoice.load(_MODEL_PATH, _CONFIG_PATH, use_cuda=False)

_EPHEMERIS_PATH = resources.files("orc_data") / "de421.bsp"
_TIMESCALE = load.timescale()
_EPHEMERIS = load_file(str(_EPHEMERIS_PATH))
_TWILIGHT_FN = almanac.dark_twilight_day(_EPHEMERIS, wgs84.latlon(*config.lat_long))


SnapShot = nt("SnapShot", "routine end")
ThemeOverride = nt("ThemeOverride", "name start end")

# --- Utilities ---


def jobs_by_type(scheduler, type):
    now = local_now()
    return [e for e in scheduler.get_jobs() if e.args and isinstance(e.args[0], type) and e.trigger.run_date > now]


def local_now():
    return datetime.now(tz=config.tz)


def log(when, source, action):
    _ACTIVITY_LOG.add(when, source, action)


def log_entries():
    return _ACTIVITY_LOG.entries


def unwrap_rule_container(f):
    def wrapper(*args):
        if isinstance(args[0], m.Routine | m.Configs):
            for e in args[0].items:
                f(e, *args[1:])
        elif len(args) > 1 and isinstance(args[1], m.Routine | m.Configs):
            for e in args[1].items:
                f(args[0], e, *args[2:])
        else:
            f(*args)

    return wrapper


# --- Device control & audio ---


def capture_lights():
    return m.Configs(*(lights.fetch_light_state(e) for e in orc.Light))


def capture_sounds():
    with Pool(max_workers=len(orc.Chromecast)) as ex:
        return m.Configs(*ex.map(chromecast.fetch_state, orc.Chromecast))


@unwrap_rule_container
def execute(rule):
    what = [rule.what] if isinstance(rule.what, Enum) else rule.what
    sleep = time.sleep if len(what) > 1 else (lambda _: 1)
    stream = {}
    for w in what:
        if os.getenv("ORC_ENABLED") and w in config.virtual_devices:
            print("Skipping virtual device:" + w.name, file=sys.stderr)
            continue

        if isinstance(w, orc.Light):
            if isinstance(rule.state, int):
                lights.update_light(w, brightness=rule.state)
            else:
                lights.update_light(w, on=rule.state == config.ON)
        elif isinstance(w, orc.Chromecast):
            if isinstance(rule.state, int):
                chromecast.set_volume(w, rule.state)
            elif rule.state == config.STOP:
                chromecast.stop(w)
            else:
                if rule.state not in stream:
                    if "http" in rule.state:
                        stream[rule.state] = (rule.state, rule.state)
                    else:
                        stream[rule.state] = chromecast.fetch_youtube_stream_metadata(rule.state)
                chromecast.play(w, *stream[rule.state])
        elif isinstance(w, orc.TV):
            if rule.state == config.ON:
                tv.on(w)
            elif rule.state == config.OFF:
                tv.off(w)
            else:
                raise Exception(f"Unsupported TV state: {rule.state!r}")
        else:
            raise Exception("Unknown type")
        sleep(0.1)


def play_alert(path):
    pygame.mixer.init()
    sound = pygame.mixer.Sound(path)
    playing = sound.play()
    while playing.get_busy():
        pygame.time.delay(100)


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


# --- State manager ---


class ConfigManager:
    def __init__(self, theme_override=None, presence=None):
        self.snapshot = None
        self._theme_override = theme_override
        self._presence = dict(presence) if presence else {}

    @property
    def theme_override(self):
        if self._theme_override and self._theme_override.end < local_now().date():
            return None
        return self._theme_override

    @theme_override.setter
    def theme_override(self, value):
        self._theme_override = value

    def replace_config(self, target_config, end):

        if not self.snapshot:
            self.snapshot = SnapShot(capture_lights(), end)
            items = ", ".join(f"{c.what.name}={c.state}" for c in self.snapshot.routine.items)
            log(local_now(), m.LogSource.SYSTEM, Log.SNAPSHOT_TAKEN.format(end=end, items=items))

        execute(target_config)

    def resume(self, target_config):
        if self.snapshot and local_now() <= self.snapshot.end:
            routine = self.snapshot.routine
            log(local_now(), m.LogSource.SYSTEM, Log.SNAPSHOT_RESTORED)
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

    def presence(self):
        return dict(self._presence)

    @property
    def present_names(self):
        cutoff = local_now() - _PRESENCE_WINDOW
        return {name for name, ts in self._presence.items() if ts >= cutoff}

    def mark_present(self, names, when):
        for name in names:
            self._presence[name] = when

    def expire_presence(self, name):
        self._presence.pop(name, None)

    def delete_all_presence(self):
        self._presence.clear()

    def active_override(self, today):
        if self.theme_override and self.theme_override.start <= today <= self.theme_override.end:
            return self.theme_override
        return None

    def calculate_theme(self, today, holidays=()):
        if override := self.active_override(today):
            return override.name

        if today.weekday() not in (5, 6):
            today_iso = today.strftime("%Y-%m-%d")
            theme_name = (
                config.THEME_DAY_OFF
                if next((e for e in holidays if e["date"] == today_iso and e["exchange"] == "NYSE"), None)
                else config.THEME_WORK_DAY
            )
        else:
            theme_name = config.THEME_DAY_OFF
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


def apply_theme_change(ctx, name, start, end):
    now = local_now()
    today = now.date()
    before = calculate_theme(ctx.config_manager, today)
    if not name:
        log(now, m.LogSource.MANUAL, Log.THEME_OVERRIDE_CLEARED)
        clear_theme_override(ctx.config_manager)
    else:
        set_theme_override(ctx.config_manager, name, start, end)
        log(now, m.LogSource.MANUAL, Log.THEME_OVERRIDE_SET.format(name=name, start=start, end=end))
    after = calculate_theme(ctx.config_manager, today)
    ctx.scheduler.remove_all_jobs()
    setup_scheduler(ctx)
    if before != after:
        replay_day(ctx.config_manager, now)


def calculate_theme(config_manager, today):
    return config_manager.calculate_theme(today, feeds.fetch_holidays(today.year))


def clear_theme_override(config_manager):
    config_manager.theme_override = None
    sqlite.delete_theme_override()


def expire_presence(config_manager, name):
    config_manager.expire_presence(name)
    sqlite.delete_presence(name)


def make_config_manager():
    row = sqlite.fetch_theme_override()
    theme_override = ThemeOverride(*row) if row else None
    return ConfigManager(theme_override=theme_override, presence=sqlite.fetch_presence())


def mark_present(config_manager, names):
    now = local_now()
    config_manager.mark_present(names, when=now)
    for name in names:
        sqlite.insert_presence(name, now)


def delete_all_presence(config_manager):
    config_manager.delete_all_presence()
    sqlite.delete_all_presence()


def replace_config_for(config_manager, id, duration):
    config_manager.replace_config(config.ad_hoc_routines[id], local_now() + duration)


def set_theme_override(config_manager, name, start, end):
    override = ThemeOverride(name, start, end)
    config_manager.theme_override = override
    sqlite.insert_theme_override(override)


# --- Scheduling & job handlers ---


@requires_ctx
def check_presence(ctx):
    pairs = [(name, host) for name, hosts in config.people.items() for host in hosts]
    if not pairs:
        return ctx.config_manager.present_names
    before = ctx.config_manager.present_names
    with Pool(max_workers=len(pairs)) as ex:
        present = {name for name, ok in ex.map(lambda nh: _safe_ping(*nh), pairs) if ok}
    mark_present(ctx.config_manager, present)
    after = ctx.config_manager.present_names
    for name in sorted(after - before):
        log(local_now(), m.LogSource.SYSTEM, Log.PRESENCE_DETECTED.format(name=name))
    for name in sorted(before - after):
        log(local_now(), m.LogSource.SYSTEM, Log.PRESENCE_LOST.format(name=name))
    return after


def get_schedule(config_manager):
    result = []
    for x in range(2):
        now = local_now() + timedelta(days=x)
        today = now.date()

        local_midnight = datetime(today.year, today.month, today.day, tzinfo=config.tz)
        day_start = _TIMESCALE.from_datetime(local_midnight)
        day_end = _TIMESCALE.from_datetime(local_midnight + timedelta(days=1))

        prev = int(_TWILIGHT_FN(day_start).item())
        times, twilight = almanac.find_discrete(day_start, day_end, _TWILIGHT_FN)
        sunrise = sunset = None

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
            cfg = config.themes.get(today.strftime("%A").lower()) or config.themes.get(calculate_theme(config_manager, today))

        for e in cfg.configs:
            if e.when == m.SUNRISE:
                time = sunrise
            elif e.when == m.SUNSET:
                time = sunset
            else:
                time = now.replace(hour=e.when.hour, minute=e.when.minute, second=0)
            result.append((time.astimezone(config.tz), e))
    return result


def next_iot_job(scheduler, present_names):
    jobs = sorted(jobs_by_type(scheduler, m.IotJob), key=lambda e: e.trigger.run_date)
    return next(
        (
            j
            for j in jobs
            if j.next_run_time
            and not any(cfg.trigger == m.Trigger.SYSTEM for cfg in j.args[0].rule.items)
            and not should_skip_for_presence(j.args[0].rule, False, present_names)
        ),
        None,
    )


@requires_ctx
def run_iot_job(job, ctx, force=False):
    rule = job.rule
    if should_skip_for_presence(rule, force, ctx.config_manager.present_names):
        absent = sorted({c.trigger for c in rule.items if c.trigger not in (None, m.Trigger.SYSTEM, m.Trigger.ANYONE)})
        detail = f"absent: {', '.join(absent)}" if absent else "no one present"
        log(local_now(), m.LogSource.IOT, Log.RULE_SKIPPED.format(rule_name=rule.name, detail=detail))
        return
    if not force:
        log(local_now(), m.LogSource.IOT, rule.name)
    ctx.config_manager.route_rule(rule, force)


def setup_scheduler(ctx):
    if not jobs_by_type(ctx.scheduler, m.IotJob):
        _rebuild_iot_schedule(ctx=ctx)
    ctx.scheduler.add_job(
        _rebuild_iot_schedule,
        CronTrigger.from_crontab("10 0 * * *"),
        replace_existing=True,
        id="iot-cron",
        name="Iot Cron",
        jobstore=JOBSTORE_MEMORY,
    )
    ctx.scheduler.add_job(
        _rebuild_cal_schedule,
        CronTrigger.from_crontab("*/5 8-18 * * *"),
        replace_existing=True,
        id="cal-cron",
        name="Calendar Cron",
        jobstore=JOBSTORE_MEMORY,
    )
    ctx.scheduler.add_job(
        check_presence,
        CronTrigger.from_crontab("5 * * * *"),
        replace_existing=True,
        id="presence-cron",
        name="Presence Cron",
        jobstore=JOBSTORE_MEMORY,
    )


def should_skip_for_presence(rule, force, present_names):
    if force or not rule.items:
        return False
    for c in rule.items:
        if not c.trigger or c.trigger == m.Trigger.SYSTEM or c.trigger in present_names:
            return False
        if c.trigger == m.Trigger.ANYONE and present_names:
            return False
    return True


@requires_ctx
def _rebuild_cal_schedule(ctx):
    _schedule_cal_tasks(ctx.scheduler, ctx.config_manager)


@requires_ctx
def _rebuild_iot_schedule(ctx):
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


@requires_ctx
def _run_cal_job(job, ctx):
    if job.event_type == m.CalendarEvent.WARNING:
        play_alert(ctx.sound_path)
    else:
        log(local_now(), m.LogSource.CALENDAR, job.summary)
        play_text(job.summary)


def _safe_ping(name, host):
    try:
        return name, discovery.ping_host(host)
    except Exception as exc:
        log(local_now(), m.LogSource.SYSTEM, Log.PRESENCE_PING_FAILED.format(name=name, exc=exc))
        return name, False


def _schedule_cal_tasks(scheduler, config_manager):
    now = local_now()
    if calculate_theme(config_manager, now.date()) == config.THEME_WORK_DAY and (now.time().minute in _CALENDAR_CHECK_MINUTES):
        events = list(itertools.islice(feeds.fetch_ical(now, timedelta(hours=20)), 50))
        warning_events = (m.CalendarEvent.from_cal(e, m.CalendarEvent.WARNING, timedelta(minutes=-2), config.tz) for e in events)
        alarm_events = (m.CalendarEvent.from_cal(e, m.CalendarEvent.ALARM, timedelta(), config.tz) for e in events)

        calendar_by_id = {e.uuid: e for e in itertools.chain.from_iterable((alarm_events, warning_events))}

        for e in jobs_by_type(scheduler, m.CalendarJob):
            if e.id not in calendar_by_id:
                scheduler.remove_job(e.id)

        for id, event in calendar_by_id.items():
            scheduler.add_job(
                _run_cal_job,
                DateTrigger(event.datetime),
                args=[m.CalendarJob(event.type, event.summary)],
                replace_existing=True,
                id=id,
                name=event.summary,
                jobstore=JOBSTORE_MEMORY,
            )


# --- Manual triggers / replay ---


def light_test():
    execute(m.Config(orc.Light, config.ON))
    time.sleep(10)


def replay_day(config_manager, now):
    jobs = sorted(get_schedule(config_manager), key=lambda x: x[0])
    present_names = config_manager.present_names
    configs = (cfg for (when, cfg) in jobs if when <= now and not should_skip_for_presence(cfg, False, present_names))
    execute(m.squish_configs(*configs))
