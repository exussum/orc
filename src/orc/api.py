import contextlib
import itertools
import math
import os
import sys
import threading
import time
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor as Pool
from dataclasses import replace
from datetime import date, datetime, timedelta
from enum import Enum
from importlib import resources  # nosemgrep: python37-compatibility-importlib2
from typing import Any
from urllib.parse import urlparse

import icmplib
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.job import Job
from apscheduler.schedulers.base import BaseScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from skyfield import almanac
from skyfield.api import load, load_file, wgs84

import orc
from orc import config, device_registry
from orc import model as m
from orc._decorators import (
    requires_ctx,
    synchronized,
    unwrap_rule_container,
)
from orc.dal import broadlink, chromecast, feeds, hubitat, sqlite
from orc.dal.bws import fetch_secrets  # noqa: F401
from orc.dal.hubitat import fetch_hubitat_config  # noqa: F401
from orc.dal.hubitat import reboot as reboot_hubitat  # noqa: F401
from orc.dal.sqlite import delete_theme_override as clear_theme_override  # noqa: F401
from orc.dal.sqlite import fetch_durations as _fetch_durations
from orc.dal.sqlite import fetch_presence as last_seen  # noqa: F401
from orc.dal.sqlite import init_db  # noqa: F401
from orc.dal.sqlite import update_avg  # noqa: F401
from orc.dal.sqlite import insert_presence as mark_present
from orc.dal.usb import play_alert, play_text
from orc.locale import Log
from orc.security import safe_domain

JOBSTORE_DEFAULT = "default"
JOBSTORE_MEMORY = "memory"

_BROADLINK_CODES = os.getenv("ORC_BROADLINK_CODES", "/etc/orc/broadlink_codes.json")
_PRESENCE_WINDOW = timedelta(hours=9)
_ACTIVITY_LOG = m.ActivityLog()
_WEATHER_TRIGGERS: frozenset[str] = frozenset(wc.value for wc in m.WeatherCondition)

_STREAM_DOMAINS: set[str] = {".googlevideo.com", urlparse(config.internal_url).hostname or "", "." + config.root_domain}

_EPHEMERIS_PATH = resources.files("orc_data") / "de421.bsp"
_TIMESCALE = load.timescale()
_EPHEMERIS = load_file(str(_EPHEMERIS_PATH))
_TWILIGHT_FN = almanac.dark_twilight_day(_EPHEMERIS, wgs84.latlon(*config.lat_long))


def fetch_durations() -> list[tuple[str, int]]:
    return [(name, math.ceil(avg)) for name, avg in _fetch_durations()]


@contextlib.contextmanager
def record_duration(name: str) -> Iterator[None]:
    start = time.perf_counter()
    yield
    update_avg(name, time.perf_counter() - start)


class ContextThreadPoolExecutor(ThreadPoolExecutor):
    def __init__(self, ctx: m.AppContext, max_workers: int = 1) -> None:
        super().__init__(max_workers=max_workers)
        self.ctx = ctx

    def _do_submit_job(self, job: Job, run_times: list[datetime]) -> Any:
        dispatch_job = job.__class__.__new__(job.__class__)
        for slot in job.__slots__:
            try:
                setattr(dispatch_job, slot, getattr(job, slot))
            except AttributeError:
                pass
        dispatch_job._jobstore_alias = job._jobstore_alias
        dispatch_job.kwargs = {**job.kwargs, "ctx": self.ctx}
        return super()._do_submit_job(dispatch_job, run_times)

    def run_now(self, job: Job, **extra_kwargs: Any) -> Any:
        return job.func(*job.args, ctx=self.ctx, **{**job.kwargs, **extra_kwargs})


# --- Utilities ---


def jobs_by_type(scheduler: BaseScheduler, type: type) -> list[Job]:
    now = local_now()
    return [e for e in scheduler.get_jobs() if e.args and isinstance(e.args[0], type) and e.trigger.run_date > now]


def local_now() -> datetime:
    return datetime.now(tz=config.tz)


def log(when: datetime, source: m.LogSource, action: str) -> None:
    _ACTIVITY_LOG.add(when, source, action)


def log_entries() -> list[m.LogEntry]:
    return list(_ACTIVITY_LOG.entries)


# --- Device control ---


def capture_lights() -> m.Configs:
    return hubitat.fetch_light_states(tuple(orc.Light))


def capture_sounds() -> m.Configs[m.SoundState]:
    with Pool(max_workers=len(orc.Chromecast)) as ex:
        return m.Configs(*ex.map(chromecast.fetch_state, orc.Chromecast))


# Dispatch handlers keyed by device-type name in orc.device_registry. Each takes
# the device, the rule, and a per-dispatch `stream` cache (shared across the
# devices in one dispatch call so a stream's metadata is fetched only once).
# Plugins register their own handlers the same way.
def _dispatch_light(w: m.DeviceEnum, rule: m.Config, stream: dict[Any, tuple[str, str]]) -> None:
    if isinstance(rule.state, int):
        hubitat.update_light(w, brightness=rule.state)
    else:
        hubitat.update_light(w, on=rule.state == m.ON)


def _dispatch_chromecast(w: m.DeviceEnum, rule: m.Config, stream: dict[Any, tuple[str, str]]) -> None:
    if isinstance(rule.state, int):
        chromecast.set_volume(w, rule.state)
    elif rule.state == m.STOP:
        chromecast.stop(w)
    elif rule.state == m.PAUSE:
        chromecast.pause(w)
    elif rule.state == m.RESUME:
        chromecast.resume(w)
    else:
        if rule.state not in stream:
            # rule.state is a stream URL or YouTube id (str) in this branch; Config.state is typed object
            stream[rule.state] = (
                (safe_domain(rule.state, _STREAM_DOMAINS), rule.state)
                if "http" in rule.state
                else chromecast.fetch_youtube_stream_metadata(rule.state)
            )
        chromecast.play(w, *stream[rule.state])


def register_core(core: device_registry.RegistryBuilder) -> None:
    """Register core's own dispatch handlers into the given registry state. Called
    from ``Config.load`` (not at import) so all registration happens on config load,
    like plugins."""
    core.register_dispatch("Light", _dispatch_light)
    core.register_dispatch("Chromecast", _dispatch_chromecast)


@unwrap_rule_container
def dispatch(rule: m.Config, force: bool = False) -> None:
    if not force and snapshot_manager.intercepts(rule):
        return
    what = [rule.what] if isinstance(rule.what, Enum) else rule.what
    sleep = time.sleep if len(what) > 1 else (lambda _: 1)
    stream: dict[Any, tuple[str, str]] = {}
    for w in what:
        if os.getenv("ORC_ENABLED") and w in config.virtual_devices:
            print("Skipping virtual device:" + w.name, file=sys.stderr)
            continue

        device_type = config.registry.devices.get(type(w).__name__)
        if device_type is None or device_type.dispatch is None:
            raise Exception("Unknown type")
        device_type.dispatch(w, rule, stream)
        sleep(0.1)


def ac_command(
    bl_device: m.DeviceEnum, state: str | None, mode: str | None = None, fan: str | None = None, temp: int | None = None
) -> None:
    if state == m.OFF:
        broadlink.ac_off(bl_device, _BROADLINK_CODES)
    else:
        broadlink.set_ac(bl_device, _BROADLINK_CODES, mode or "cool", fan or "low", temp or 75)


def device_command(id: str, state: str | None) -> None:
    # Find the device across dispatch-handled types and run its registered handler
    # directly (no snapshot interception), so plugin device types work without core
    # knowing them. state is an int level (brightness/volume) or an ON/OFF/STOP string.
    parsed: Any = int(state) if state and state.isdigit() else state
    for device_type in config.registry.devices.values():
        if device_type.dispatch is None:
            continue
        try:
            member = device_type.cls[id]
        except KeyError:
            continue
        device_type.dispatch(member, m.Config(member, parsed), {})
        return


# --- State manager ---

ORC_SYSTEM_SNAPSHOT = "ORC_SYSTEM_SNAPSHOT"


class SnapshotManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.snapshots: dict[str, m.SnapShot] = {}

    @synchronized
    def replace_config(self, name: str, target_config: m.Configs, end: datetime) -> None:

        if name not in self.snapshots:
            self.snapshots[name] = m.SnapShot(capture_lights(), end)
            items = ", ".join(f"{c.what.name}={c.state}" for c in self.snapshots[name].routine.items if c.state != m.OFF)  # type: ignore[union-attr]  # captured light states are always enum members, not the class/set arm
            log(local_now(), m.LogSource.SYSTEM, Log.SNAPSHOT_TAKEN.format(name=name, end=end, items=items or Log.SNAPSHOT_ALL_OFF))

        dispatch(target_config, force=True)

    @synchronized
    def get(self, name: str) -> m.SnapShot | None:
        snapshot = self.snapshots.pop(name, None)
        if snapshot and local_now() <= snapshot.end:
            return snapshot
        return None

    @synchronized
    def resume(self, name: str, target_config: m.Configs) -> None:
        snapshot = self.snapshots.pop(name, None)

        if snapshot and local_now() <= snapshot.end:
            routine = snapshot.routine
            log(local_now(), m.LogSource.SYSTEM, Log.SNAPSHOT_RESTORED.format(name=name))
        else:
            routine = target_config
        dispatch(routine, force=True)

    @synchronized
    def update_snapshot(self, name: str, rule: m.Config) -> None:
        snapshot = self.snapshots[name]

        what = [rule.what] if isinstance(rule.what, Enum) else rule.what
        items = {e.what: e for e in snapshot.routine.items}

        # Explode out the rule w/o creating a sub config explicitly
        items.update({e: replace(rule, what=e) for e in what})
        self.snapshots[name] = snapshot._replace(routine=m.Configs(*items.values()))

    @synchronized
    def intercepts(self, rule: m.Config) -> bool:
        if ORC_SYSTEM_SNAPSHOT in self.snapshots and rule.trigger == m.Trigger.SYSTEM:
            self.update_snapshot(ORC_SYSTEM_SNAPSHOT, rule)
        elif ORC_SYSTEM_SNAPSHOT in self.snapshots and local_now() > self.snapshots[ORC_SYSTEM_SNAPSHOT].end:
            self.snapshots.pop(ORC_SYSTEM_SNAPSHOT, None)
        elif ORC_SYSTEM_SNAPSHOT in self.snapshots:
            what = [rule.what] if isinstance(rule.what, Enum) else rule.what
            kinds = ", ".join(sorted({type(e).__name__ for e in what}))
            log(local_now(), m.LogSource.SYSTEM, Log.RULE_SUPPRESSED.format(kinds=kinds))
            return True
        return False


snapshot_manager = SnapshotManager()


def current_theme_override() -> m.ThemeOverride | None:
    row = sqlite.fetch_theme_override()
    if not row:
        return None
    override = m.ThemeOverride(*row)
    return override if override.end >= local_now().date() else None


def active_theme_override(today: date) -> m.ThemeOverride | None:
    cur = current_theme_override()
    return cur if cur and cur.start <= today <= cur.end else None


def calculate_theme(today: date) -> str:
    if override := active_theme_override(today):
        return override.name
    if today.weekday() in (5, 6):
        return m.THEME_DAY_OFF
    today_iso = today.strftime("%Y-%m-%d")
    is_holiday = any(e["date"] == today_iso and e["exchange"] == "NYSE" for e in feeds.fetch_holidays(today.year))
    return m.THEME_DAY_OFF if is_holiday else m.THEME_WORK_DAY


def set_theme_override(name: str, start: date, end: date) -> None:
    sqlite.insert_theme_override(m.ThemeOverride(name, start, end))


def present_names() -> set[str]:
    cutoff = local_now() - _PRESENCE_WINDOW
    return {name for name, ts in sqlite.fetch_presence().items() if ts >= cutoff}


def expire_presence(names: list[str], force: bool = False) -> None:
    sqlite.delete_presence(names, local_now(), force)


def delete_all_presence() -> None:
    sqlite.delete_all_presence(local_now())


def apply_theme_change(ctx: m.AppContext, name: str, start: date | None, end: date | None) -> None:
    now = local_now()
    today = now.date()
    before = calculate_theme(today)
    if not name:
        log(now, m.LogSource.MANUAL, Log.THEME_OVERRIDE_CLEARED)
        clear_theme_override()
    else:
        assert start is not None and end is not None  # a named theme override always carries a start/end window
        set_theme_override(name, start, end)
        log(now, m.LogSource.MANUAL, Log.THEME_OVERRIDE_SET.format(name=name, start=start, end=end))
    after = calculate_theme(today)
    ctx.scheduler.remove_all_jobs()
    setup_scheduler(ctx)
    if before != after:
        replay_day(now)


def check_presence(silent: bool = False) -> set[str]:
    pairs = [(name, host) for name, hosts in config.people.items() for host in hosts]
    if not pairs:
        return present_names()
    before = present_names()
    with Pool(max_workers=len(pairs)) as ex:
        present = {name for name, ok in ex.map(lambda nh: _safe_ping(*nh), pairs) if ok}
    mark_present(present, local_now())
    after = present_names()

    if not silent:
        if detected := sorted(after - before):
            log(local_now(), m.LogSource.SYSTEM, Log.PRESENCE_DETECTED.format(name=", ".join(detected)))
        if lost := sorted(before - after):
            log(local_now(), m.LogSource.SYSTEM, Log.PRESENCE_LOST.format(name=", ".join(lost)))
    return after


def get_schedule() -> list[tuple[datetime, m.Routine]]:
    result: list[tuple[datetime, m.Routine]] = []
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

        assert cfg is not None  # the resolved theme is always present in config
        for e in cfg.configs:
            if e.when == m.SUNRISE:
                time = sunrise
            elif e.when == m.SUNSET:
                time = sunset
            else:
                time = now.replace(hour=e.when.hour, minute=e.when.minute, second=0)  # type: ignore[attr-defined]  # e.when is a datetime.time here (normalized in __post_init__), not the SUNRISE/SUNSET str
            if time is None:
                continue
            result.append((time.astimezone(config.tz), e))
    return result


def next_iot_job(scheduler: BaseScheduler, present_names: set[str]) -> Job | None:
    jobs = sorted(jobs_by_type(scheduler, m.IotJob), key=lambda e: e.trigger.run_date)
    return next(
        (
            j
            for j in jobs
            if j.next_run_time
            and not any(cfg.trigger == m.Trigger.SYSTEM for cfg in j.args[0].rule.items)
            and matching_items(j.args[0].rule, False, j.next_run_time, present_names)
        ),
        None,
    )


@requires_ctx
def run_iot_job(job: m.IotJob, ctx: m.AppContext, force: bool = False) -> None:
    rule = job.rule
    now = local_now()
    if not (matched := matching_items(rule, force, now, present_names())):
        unmet = sorted({c.trigger for c in rule.items if c.trigger not in (None, m.Trigger.SYSTEM, m.Trigger.ANYONE)})
        detail = ", ".join(unmet) if unmet else "no conditions met"
        log(now, m.LogSource.ROUTINE, Log.RULE_SKIPPED.format(rule_name=rule.name, detail=detail))
        return
    elif not force:
        if weather_triggers := {c.trigger for c in matched if c.trigger in _WEATHER_TRIGGERS}:
            log(now, m.LogSource.ROUTINE, f"{rule.name} (weather: {', '.join(sorted(weather_triggers))})")
        else:
            log(now, m.LogSource.ROUTINE, rule.name)
    dispatch(replace(rule, items=matched), force=force)


def setup_scheduler(ctx: m.AppContext) -> None:
    if not jobs_by_type(ctx.scheduler, m.IotJob):
        rebuild_iot_schedule(ctx=ctx)
    crons = (
        (rebuild_iot_schedule, "10 0 * * *", "iot-cron", "Iot Cron"),
        (rebuild_cal_schedule, "10,25,40,55 8-18 * * *", "cal-cron", "Calendar Cron"),
        (_check_presence_job, "5 * * * *", "presence-cron", "Presence Cron"),
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


def matched_presence(rule: m.Routine, people: set[str] | None = None) -> tuple[m.Config, ...]:
    return tuple(
        c
        for c in rule.items
        if c.trigger
        and c.trigger != m.Trigger.SYSTEM
        and c.trigger not in _WEATHER_TRIGGERS
        and (people is None or (c.trigger == m.Trigger.ANYONE and people) or c.trigger in people)
    )


def is_absent(rule: m.Routine, present_names: set[str]) -> bool:
    presence = matched_presence(rule)
    return bool(presence) and not matched_presence(rule, present_names)


def matched_weather(rule: m.Routine, now: datetime) -> tuple[m.Config, ...]:
    return tuple(c for c in rule.items if c.trigger in _WEATHER_TRIGGERS and c.trigger in feeds.fetch_weather(now, *config.lat_long))


def matching_items(rule: m.Routine, force: bool, now: datetime, pnames: set[str]) -> Sequence[m.Config]:
    if force:
        return rule.items
    system = tuple(c for c in rule.items if not c.trigger or c.trigger == m.Trigger.SYSTEM)
    presence = matched_presence(rule, pnames)
    weather = matched_weather(rule, now) if pnames else ()
    return system + presence + weather


@requires_ctx
def rebuild_cal_schedule(ctx: m.AppContext) -> None:
    _schedule_cal_tasks(ctx.scheduler)


@requires_ctx
def rebuild_iot_schedule(ctx: m.AppContext) -> None:
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


def light_test() -> None:
    dispatch(m.Config(orc.Light, m.ON), force=True)
    time.sleep(10)


def replay_day(now: datetime) -> None:
    jobs = sorted(get_schedule(), key=lambda x: x[0])
    present = present_names()
    # replace() keeps Routine type; squish_configs only reads .items, which Routine and Configs share
    configs = (replace(cfg, items=matching_items(cfg, False, now, present)) for (when, cfg) in jobs if when <= now)
    dispatch(m.squish_configs(*configs), force=True)


@requires_ctx
def _run_cal_job(job: m.CalendarJob, ctx: m.AppContext) -> None:
    if job.event_type == m.CalendarEvent.WARNING:
        play_alert(ctx.sound_path)
    else:
        log(local_now(), m.LogSource.CALENDAR, job.summary)
        play_text(job.summary)


@requires_ctx
def _check_presence_job(ctx: m.AppContext) -> set[str]:
    return check_presence()


def _safe_ping(name: str, host: str) -> tuple[str, bool]:
    try:
        return name, icmplib.ping(host, count=2, interval=0.1, timeout=1, privileged=True).is_alive
    except Exception as exc:
        log(local_now(), m.LogSource.SYSTEM, Log.PRESENCE_PING_FAILED.format(name=name, exc=exc))
        return name, False


def _schedule_cal_tasks(scheduler: BaseScheduler) -> None:
    now = local_now()
    if calculate_theme(now.date()) != m.THEME_WORK_DAY:
        return

    # fetch_ical's `end` is typed as datetime, but recurring_ical_events.between accepts a timedelta window at runtime
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
