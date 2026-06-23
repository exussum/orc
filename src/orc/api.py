import copy
import itertools
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor as Pool
from dataclasses import replace
from datetime import datetime, timedelta
from enum import Enum
from importlib import resources

import icmplib
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from skyfield import almanac
from skyfield.api import load, load_file, wgs84

import orc
from orc import config
from orc import model as m
from orc._decorators import (
    requires_ctx,
    synchronized,
    unwrap_rule_container,
)
from orc.dal import chromecast, feeds, hubitat, sqlite, tv, usb, yolink
from orc.dal.bws import fetch_secrets  # noqa: F401
from orc.dal.chromecast import pause, resume, stop  # noqa: F401
from orc.dal.hubitat import fetch_hubitat_config  # noqa: F401
from orc.dal.hubitat import reboot as reboot_hubitat  # noqa: F401
from orc.dal.lgtv import pair as pair_lg_tv  # noqa: F401
from orc.dal.sqlite import delete_theme_override as clear_theme_override  # noqa: F401
from orc.dal.sqlite import fetch_presence as last_seen  # noqa: F401
from orc.dal.sqlite import init_db  # noqa: F401
from orc.dal.sqlite import insert_presence as mark_present
from orc.dal.tv import fetch_macs  # noqa: F401
from orc.dal.usb import play_alert, play_text
from orc.locale import Log

_PRESENCE_WINDOW = timedelta(hours=9)
_ACTIVITY_LOG = m.ActivityLog()
_WEATHER_TRIGGERS = frozenset(wc.value for wc in m.WeatherCondition)

_EPHEMERIS_PATH = resources.files("orc_data") / "de421.bsp"
_TIMESCALE = load.timescale()
_EPHEMERIS = load_file(str(_EPHEMERIS_PATH))
_TWILIGHT_FN = almanac.dark_twilight_day(_EPHEMERIS, wgs84.latlon(*config.lat_long))


JOBSTORE_DEFAULT = "default"
JOBSTORE_MEMORY = "memory"


class ContextThreadPoolExecutor(ThreadPoolExecutor):
    def __init__(self, ctx: "m.AppContext", max_workers=1):
        super().__init__(max_workers=max_workers)
        self.ctx = ctx

    def _do_submit_job(self, job, run_times):
        dispatch_job = copy.copy(job)
        dispatch_job._jobstore_alias = job._jobstore_alias
        dispatch_job.kwargs = {**job.kwargs, "ctx": self.ctx}
        return super()._do_submit_job(dispatch_job, run_times)

    def run_now(self, job, **extra_kwargs):
        return job.func(*job.args, ctx=self.ctx, **{**job.kwargs, **extra_kwargs})


# --- Utilities ---


def jobs_by_type(scheduler, type):
    now = local_now()
    return [e for e in scheduler.get_jobs() if e.args and isinstance(e.args[0], type) and e.trigger.run_date > now]


def local_now():
    return datetime.now(tz=config.tz)


def log(when, source, action):
    _ACTIVITY_LOG.add(when, source, action)


def log_entries():
    return list(_ACTIVITY_LOG.entries)


# --- YoLink leak sensors ---


def test_yolink(name):
    return yolink.simulate_transition(name)


_YOLINK_BATTERY_LOW_THRESHOLD = 1
_YOLINK_SIGNAL_WEAK_THRESHOLD = -90


def _on_yolink_transition(name, kind, old, new):
    msg = None
    if kind == "connection":
        msg = (Log.YOLINK_CONNECTED if new == "connected" else Log.YOLINK_DISCONNECTED).format(name=name)
    elif kind == "leak" and new in (yolink.STATE_WET, yolink.STATE_DRY):
        msg = (Log.YOLINK_WATER_DETECTED if new == yolink.STATE_WET else Log.YOLINK_WATER_CLEARED).format(name=name)
        if new == yolink.STATE_WET:
            log(local_now(), m.LogSource.IOT, msg)
            play_text(msg, level=config.AUDIO_FATAL)
            return
    elif kind == "battery":
        old_low = old is not None and old <= _YOLINK_BATTERY_LOW_THRESHOLD
        new_low = new <= _YOLINK_BATTERY_LOW_THRESHOLD
        if new_low and not old_low:
            msg = Log.YOLINK_LOW_BATTERY.format(name=name, battery=new)
        elif old_low and not new_low:
            msg = Log.YOLINK_BATTERY_RESTORED.format(name=name, battery=new)
    elif kind == "signal":
        old_weak = old is not None and old <= _YOLINK_SIGNAL_WEAK_THRESHOLD
        new_weak = new <= _YOLINK_SIGNAL_WEAK_THRESHOLD
        if new_weak and not old_weak:
            msg = Log.YOLINK_WEAK_SIGNAL.format(name=name, signal=new)
        elif old_weak and not new_weak:
            msg = Log.YOLINK_SIGNAL_RESTORED.format(name=name, signal=new)
    elif kind == "interval" and old is not None:
        msg = Log.YOLINK_INTERVAL_CHANGED.format(name=name, interval=new)
    elif kind == "online" and old is not None:
        msg = (Log.YOLINK_ONLINE if new else Log.YOLINK_OFFLINE).format(name=name)

    if msg:
        log(local_now(), m.LogSource.IOT, msg)
        play_text(msg)


def start_yolink():
    yolink.set_transition_callback(_on_yolink_transition)
    yolink.start()


# --- Device control ---


def capture_lights():
    return hubitat.fetch_light_states(tuple(orc.Light))


def capture_sounds():
    with Pool(max_workers=len(orc.Chromecast)) as ex:
        return m.Configs(*ex.map(chromecast.fetch_state, orc.Chromecast))


def capture_leak_sensors():
    return yolink.snapshot()


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
                hubitat.update_light(w, brightness=rule.state)
            else:
                hubitat.update_light(w, on=rule.state == config.ON)
        elif isinstance(w, orc.Chromecast):
            if isinstance(rule.state, int):
                chromecast.set_volume(w, rule.state)
            elif rule.state == config.STOP:
                chromecast.stop(w)
            else:
                if rule.state not in stream:
                    stream[rule.state] = (
                        (rule.state, rule.state) if "http" in rule.state else chromecast.fetch_youtube_stream_metadata(rule.state)
                    )
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


# --- State manager ---


class SnapshotManager:
    def __init__(self):
        self._lock = threading.RLock()
        self.snapshot = None

    @synchronized
    def replace_config(self, target_config, end):

        if not self.snapshot:
            self.snapshot = m.SnapShot(capture_lights(), end)
            items = ", ".join(f"{c.what.name}={c.state}" for c in self.snapshot.routine.items)
            log(local_now(), m.LogSource.SYSTEM, Log.SNAPSHOT_TAKEN.format(end=end, items=items))

        execute(target_config)

    @synchronized
    def resume(self, target_config):
        if self.snapshot and local_now() <= self.snapshot.end:
            routine = self.snapshot.routine
            log(local_now(), m.LogSource.SYSTEM, Log.SNAPSHOT_RESTORED)
        else:
            routine = target_config
        self.snapshot = None
        execute(routine)

    @synchronized
    def update_snapshot(self, rule):
        what = [rule.what] if isinstance(rule.what, Enum) else rule.what
        items = {e.what: e for e in self.snapshot.routine.items}

        # Explode out the rule w/o creating a sub config explicitly
        items.update({e: replace(rule, what=e) for e in what})
        self.snapshot = self.snapshot._replace(routine=m.Configs(*items.values()))

    @synchronized
    @unwrap_rule_container
    def route_rule(self, rule, force):
        if self.snapshot and rule.trigger == m.Trigger.SYSTEM:
            self.update_snapshot(rule)
        elif self.snapshot and local_now() > self.snapshot.end:
            self.snapshot = None
        elif self.snapshot and not force:
            return
        execute(rule)


def current_theme_override():
    row = sqlite.fetch_theme_override()
    if not row:
        return None
    override = m.ThemeOverride(*row)
    return override if override.end >= local_now().date() else None


def active_theme_override(today):
    cur = current_theme_override()
    return cur if cur and cur.start <= today <= cur.end else None


def calculate_theme(today):
    if override := active_theme_override(today):
        return override.name
    if today.weekday() in (5, 6):
        return config.THEME_DAY_OFF
    today_iso = today.strftime("%Y-%m-%d")
    is_holiday = any(e["date"] == today_iso and e["exchange"] == "NYSE" for e in feeds.fetch_holidays(today.year))
    return config.THEME_DAY_OFF if is_holiday else config.THEME_WORK_DAY


def set_theme_override(name, start, end):
    sqlite.insert_theme_override(m.ThemeOverride(name, start, end))


def present_names():
    cutoff = local_now() - _PRESENCE_WINDOW
    return {name for name, ts in sqlite.fetch_presence().items() if ts >= cutoff}


def expire_presence(names):
    sqlite.delete_presence(names, local_now())


def delete_all_presence():
    sqlite.delete_all_presence(local_now())


def replace_config_for(snapshot_manager, id, duration):
    snapshot_manager.replace_config(config.ad_hoc_routines[id], local_now() + duration)


def apply_theme_change(ctx, name, start, end):
    now = local_now()
    today = now.date()
    before = calculate_theme(today)
    if not name:
        log(now, m.LogSource.MANUAL, Log.THEME_OVERRIDE_CLEARED)
        clear_theme_override()
    else:
        set_theme_override(name, start, end)
        log(now, m.LogSource.MANUAL, Log.THEME_OVERRIDE_SET.format(name=name, start=start, end=end))
    after = calculate_theme(today)
    ctx.scheduler.remove_all_jobs()
    setup_scheduler(ctx)
    if before != after:
        replay_day(now)


@requires_ctx
def check_presence(ctx):
    pairs = [(name, host) for name, hosts in config.people.items() for host in hosts]
    if not pairs:
        return present_names()
    before = present_names()
    with Pool(max_workers=len(pairs)) as ex:
        present = {name for name, ok in ex.map(lambda nh: _safe_ping(*nh), pairs) if ok}
    mark_present(present, local_now())
    after = present_names()
    for name in sorted(after - before):
        log(local_now(), m.LogSource.SYSTEM, Log.PRESENCE_DETECTED.format(name=name))
    for name in sorted(before - after):
        log(local_now(), m.LogSource.SYSTEM, Log.PRESENCE_LOST.format(name=name))
    return after


def get_schedule():
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

        if override := active_theme_override(today):
            cfg = config.themes.get(override.name)
        else:
            cfg = config.themes.get(today.strftime("%A").lower()) or config.themes.get(calculate_theme(today))

        for e in cfg.configs:
            if e.when == m.SUNRISE:
                time = sunrise
            elif e.when == m.SUNSET:
                time = sunset
            else:
                time = now.replace(hour=e.when.hour, minute=e.when.minute, second=0)
            if time is None:
                continue
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
    if should_skip(rule, force, present_names()):
        unmet = sorted({c.trigger for c in rule.items if c.trigger not in (None, m.Trigger.SYSTEM, m.Trigger.ANYONE)})
        detail = ", ".join(unmet) if unmet else "no conditions met"
        log(local_now(), m.LogSource.IOT, Log.RULE_SKIPPED.format(rule_name=rule.name, detail=detail))
        return
    if not force:
        log(local_now(), m.LogSource.IOT, rule.name)
    ctx.snapshot_manager.route_rule(rule, force)


def setup_scheduler(ctx):
    if not jobs_by_type(ctx.scheduler, m.IotJob):
        _rebuild_iot_schedule(ctx=ctx)
    crons = (
        (_rebuild_iot_schedule, "10 0 * * *", "iot-cron", "Iot Cron"),
        (_rebuild_cal_schedule, "10,25,40,55 8-18 * * *", "cal-cron", "Calendar Cron"),
        (check_presence, "5 * * * *", "presence-cron", "Presence Cron"),
    )
    for func, crontab, job_id, name in crons:
        ctx.scheduler.add_job(
            func,
            CronTrigger.from_crontab(crontab, timezone=config.tz),
            replace_existing=True,
            id=job_id,
            name=name,
            jobstore=JOBSTORE_MEMORY,
        )


def should_skip_for_presence(rule, force, present_names):
    """Presence-only check used for scheduling display; weather triggers are not treated as absent."""
    if force or not rule.items:
        return False
    for c in rule.items:
        if not c.trigger or c.trigger == m.Trigger.SYSTEM or c.trigger in _WEATHER_TRIGGERS:
            return False
        if c.trigger == m.Trigger.ANYONE and present_names:
            return False
        if c.trigger in present_names:
            return False
    return True


def should_skip(rule, force, present_names):
    """Unified presence + real-time weather check used at rule execution time."""
    if force or not rule.items:
        return False
    weather_triggers = [c.trigger for c in rule.items if c.trigger in _WEATHER_TRIGGERS]
    if weather_triggers:
        try:
            weather_conditions = feeds.fetch_weather(*config.lat_long)
        except Exception:
            weather_conditions = frozenset()
    else:
        weather_conditions = frozenset()
    for c in rule.items:
        if not c.trigger or c.trigger == m.Trigger.SYSTEM:
            return False
        if c.trigger == m.Trigger.ANYONE and present_names:
            return False
        if c.trigger in present_names:
            return False
        if c.trigger in weather_conditions:
            return False
    return True


@requires_ctx
def _rebuild_cal_schedule(ctx):
    _schedule_cal_tasks(ctx.scheduler)


@requires_ctx
def _rebuild_iot_schedule(ctx):
    now = local_now()
    for time, rule in get_schedule():
        if now <= time:
            ctx.scheduler.add_job(
                run_iot_job,
                DateTrigger(time, timezone=config.tz),
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
        return name, icmplib.ping(host, count=2, interval=0.1, timeout=1, privileged=True).is_alive
    except Exception as exc:
        log(local_now(), m.LogSource.SYSTEM, Log.PRESENCE_PING_FAILED.format(name=name, exc=exc))
        return name, False


def _schedule_cal_tasks(scheduler):
    now = local_now()
    if calculate_theme(now.date()) != config.THEME_WORK_DAY:
        return

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
            DateTrigger(event.datetime, timezone=config.tz),
            args=[m.CalendarJob(event.type, event.summary)],
            replace_existing=True,
            id=id,
            name=event.summary,
            jobstore=JOBSTORE_MEMORY,
        )


def light_test():
    execute(m.Config(orc.Light, config.ON))
    time.sleep(10)


def replay_day(now):
    jobs = sorted(get_schedule(), key=lambda x: x[0])
    present = present_names()
    configs = (cfg for (when, cfg) in jobs if when <= now and not should_skip_for_presence(cfg, False, present))
    execute(m.squish_configs(*configs))
