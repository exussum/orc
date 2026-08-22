import contextlib
import math
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor as Pool
from dataclasses import replace
from datetime import date, datetime, timedelta
from enum import Enum
from importlib import resources  # nosemgrep: python37-compatibility-importlib2
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from apscheduler.job import Job
from apscheduler.schedulers.base import BaseScheduler
from skyfield import almanac
from skyfield.api import load, load_file, wgs84

import orc
from orc import config
from orc import model as m
from orc import plugins
from orc.dal import net, sqlite
from orc.dal.audio import play_alert, play_text  # noqa: F401
from orc.dal.scheduler import schedule_cron, schedule_once
from orc.dal.sqlite import connection  # noqa: F401
from orc.dal.sqlite import init_db  # noqa: F401
from orc.dal.sqlite import delete_theme_override as clear_theme_override  # noqa: F401
from orc.dal.sqlite import fetch_durations as _fetch_durations
from orc.dal.sqlite import fetch_presence as last_seen  # noqa: F401
from orc.dal.sqlite import insert_presence as mark_present
from orc.dal.sqlite import update_avg
from orc.declarations import Declarations
from orc.decorators import (
    requires_ctx,
    synchronized,
    unwrap_rule_container,
)
from orc.locale import Log
from orc.security import safe_domain

JOBSTORE_DEFAULT = "default"
JOBSTORE_MEMORY = "memory"

DEFAULT_ALERT_PATH = str((Path(__file__).parent / "static" / "alert.wav").resolve())

_PRESENCE_WINDOW = timedelta(hours=9)
_ACTIVITY_LOG = m.ActivityLog()
_WEATHER_TRIGGERS: frozenset[str] = frozenset(wc.value for wc in m.WeatherCondition)

_STREAM_DOMAINS: set[str] = {".googlevideo.com", urlparse(config.settings.base_url).hostname or "", "." + config.settings.lan_domain}

_TIMESCALE = load.timescale()
_EPHEMERIS = load_file(str(resources.files("orc_data") / "de421.bsp"))
_TWILIGHT_FN = almanac.dark_twilight_day(_EPHEMERIS, wgs84.latlon(config.settings.lat, config.settings.long))


def duration_stats() -> dict[str, tuple[int, float]]:
    """name -> (samples, average seconds); job names and command topics alike."""
    return {name: (samples, avg) for name, samples, avg in _fetch_durations()}


def fetch_durations() -> list[tuple[str, int]]:
    return [(name, math.ceil(avg)) for name, (_, avg) in duration_stats().items()]


@contextlib.contextmanager
def record_duration(name: str) -> Iterator[None]:
    start = time.perf_counter()
    yield
    update_avg(name, time.perf_counter() - start)


# --- Utilities ---


def jobs_by_type(scheduler: BaseScheduler, type: type) -> list[Job]:
    now = local_now()
    return [e for e in scheduler.get_jobs() if e.args and isinstance(e.args[0], type) and e.trigger.run_date > now]


def local_now() -> datetime:
    return datetime.now(tz=config.settings.tz)


def log(source: m.LogSourceEnum, action: str) -> m.LogEntry:
    return _ACTIVITY_LOG.add(local_now(), source, action)


def log_entries() -> list[m.LogEntry]:
    return list(_ACTIVITY_LOG.entries)


# --- Device control ---


def add_listener(fn: m.Listener) -> None:
    config.providers.mqtt.add_listener(fn)


def device_states() -> list[m.DeviceState]:
    return config.providers.mqtt.snapshot()


def capture_lights() -> m.Configs:
    return config.providers.mqtt.fetch_light_states(tuple(orc.Light))


def capture_sounds() -> m.Configs[m.SoundState]:
    if not len(orc.Chromecast):
        return m.Configs()
    with Pool(max_workers=len(orc.Chromecast)) as ex:
        return m.Configs(*ex.map(config.providers.chromecast.fetch_state, orc.Chromecast))


# Dispatch handlers keyed by device-type name in orc.declarations. Each takes the
# AppContext, the device, the rule, and a per-dispatch `stream` cache (shared across
# the devices in one dispatch call so a stream's metadata is fetched only once).
# Plugins register their own handlers the same way.
def _dispatch_light(ctx: m.AppContext, w: m.DeviceEnum, rule: m.Config, stream: dict[Any, tuple[str, str]]) -> None:
    if isinstance(rule.state, int):
        config.providers.mqtt.publish_light(w, brightness=rule.state)
    else:
        config.providers.mqtt.publish_light(w, on=rule.state == m.ON)


def _dispatch_chromecast(ctx: m.AppContext, w: m.DeviceEnum, rule: m.Config, stream: dict[Any, tuple[str, str]]) -> None:
    if isinstance(rule.state, int):
        config.providers.chromecast.set_volume(w, rule.state)
    elif rule.state == m.STOP:
        config.providers.chromecast.stop(w)
    elif rule.state == m.PAUSE:
        config.providers.chromecast.pause(w)
    elif rule.state == m.RESUME:
        config.providers.chromecast.resume(w)
    else:
        if rule.state not in stream:
            # rule.state is a stream URL or YouTube id (str) in this branch; Config.state is typed object
            stream[rule.state] = (
                (safe_domain(rule.state, _STREAM_DOMAINS), rule.state)
                if "http" in rule.state
                else config.providers.chromecast.fetch_youtube_stream_metadata(rule.state)
            )
        config.providers.chromecast.play(w, *stream[rule.state])


def add_state_provider(title: str, provider: Callable[[], Any]) -> None:
    config.registry.state_providers[title] = provider


def declare_core(declarations: Declarations) -> None:
    declarations.declare_dispatch("Light", _dispatch_light)
    declarations.declare_dispatch("Chromecast", _dispatch_chromecast)


def resolve_run_action(
    ctx: m.AppContext, id: str, *, device: str | None = None, hub_origin: bool = False
) -> tuple[Callable[[m.LogEntry], None], timedelta] | None:
    if id == ORC_SYSTEM_SNAPSHOT:
        return lambda entry: ctx.snapshot_manager.resume(ORC_SYSTEM_SNAPSHOT, config.default_config), timedelta()
    elif (plugin := config.plugin(id)) is not None:
        return lambda entry: plugins.execute_plugin(ctx, plugin, device), plugin.delay
    elif id in config.schedule_routines:
        return lambda entry: run_schedule_routine(config.schedule_routines[id], entry, force=True), timedelta()
    elif id in config.ad_hoc_routines:
        routine = config.ad_hoc_routines[id]
        if hub_origin and routine.snapshot and not ctx.snapshot_manager.active(ORC_SYSTEM_SNAPSHOT):
            # Don't stack snapshots
            snap = routine.snapshot
            return lambda entry: ctx.snapshot_manager.replace_config(ORC_SYSTEM_SNAPSHOT, routine, local_now() + snap, id), timedelta()
        base = (config.reset_config,) if routine.reset else ()
        return lambda entry: dispatch(m.squish_configs(*base, routine), force=True, entry=entry), routine.delay
    return None


def run_action(ctx: m.AppContext, id: str, *, device: str | None = None, hub_origin: bool = False, skip_delay: bool = False) -> bool:
    resolved = resolve_run_action(ctx, id, device=device, hub_origin=hub_origin)
    if resolved is None:
        return False
    action, delay = resolved
    if skip_delay:
        delay = timedelta()

    @requires_ctx
    def run(ctx: m.AppContext) -> None:
        action(log(m.LogSource.MANUAL, f"`{_RUN_DISPLAY.get(id, id)}`"))

    with record_duration(id):
        if delay:
            when = local_now() + delay
            log(m.LogSource.MANUAL, Log.TASK_QUEUED.format(id=id, when=when))
            job_id = f"run-{id}-{when.isoformat()}"
            schedule_once(ctx.scheduler, run, when, id=job_id, jobstore=JOBSTORE_MEMORY)
        else:
            run(ctx=ctx)
    return True


def wire_buttons(ctx: m.AppContext) -> None:
    mapping = {(what.value, button, event): action for (what, button, event), action in config.remotes.items()}

    def on_button(device_id: int, button: int, event_type: str) -> None:
        action = mapping.get((device_id, button, event_type))
        if action is not None and not run_action(ctx, action, hub_origin=True):
            log(m.LogSource.SYSTEM, Log.BUTTON_ACTION_UNKNOWN.format(id=action))

    config.providers.mqtt.add_button_listener(on_button)


def wire_external_log() -> None:
    def on_external(device: m.DeviceState, attribute: str, old: Any, new: Any) -> None:
        action = Log.EXTERNAL_CHANGE.format(device=device.name, attribute=attribute, old=old, new=new)
        last = next(iter(_ACTIVITY_LOG.entries), None)
        if not (
            last is not None
            and last.source is m.LogSource.EXTERNAL
            and local_now() - (last.children or [last])[-1].timestamp < timedelta(seconds=5)
        ):
            last = log(m.LogSource.EXTERNAL, Log.EXTERNAL_DETECTED)
        last.add(m.LogSource.EXTERNAL, action)

    config.providers.mqtt.add_external_listener(on_external)


@unwrap_rule_container
def dispatch(rule: m.Config, force: bool = False, entry: m.LogEntry | None = None) -> None:
    if not force and snapshot_manager.intercepts(rule):
        return
    what = [rule.what] if isinstance(rule.what, Enum) else rule.what
    stream: dict[Any, tuple[str, str]] = {}

    def one(w: m.DeviceEnum) -> None:
        if w in config.virtual_devices:
            if entry is not None:
                entry.add(m.LogSource.SYSTEM, Log.VIRTUAL_DEVICE_SKIPPED.format(device=w.name))
            return

        device_type = config.registry.devices.get(type(w).__name__)
        if device_type is None or device_type.dispatch is None:
            raise Exception("Unknown type")
        try:
            device_type.dispatch(config.registry.ctx, w, rule, stream)
        except Exception as exc:
            action = Log.DISPATCH_FAILED.format(device=w.name, exc=exc)
            if entry is not None:
                entry.add(m.LogSource.SYSTEM, action)
            else:
                log(m.LogSource.SYSTEM, action)

    with Pool(max_workers=max(1, len(what))) as ex:
        list(ex.map(one, what))


def reboot_hubitat() -> None:
    config.providers.hubitat.reboot()


def fetch_retry_stats() -> tuple[m.RetryStats, ...]:
    return config.providers.hubitat.fetch_retry_stats()


def tv_toggle(bl_device: m.DeviceEnum) -> None:
    config.providers.blaster.tv_toggle(bl_device, config.settings.broadlink_codes)


def ac_command(
    bl_device: m.DeviceEnum, state: str | None, mode: str | None = None, fan: str | None = None, temp: int | None = None
) -> None:
    if state == m.OFF:
        config.providers.blaster.ac_off(bl_device, config.settings.broadlink_codes)
    else:
        config.providers.blaster.set_ac(bl_device, config.settings.broadlink_codes, mode or "cool", fan or "low", temp or 75)


def device_command(id: str, state: str | None) -> None:
    # Find the device across dispatch-handled types and run its registered handler
    # directly (no snapshot interception), so plugin device types work without core
    # knowing them. state is an int level (brightness/volume) or an ON/OFF/STOP string.
    parsed: Any = int(state) if state and state.isdigit() else state
    for device_type in config.registry.devices.values():
        if device_type.dispatch is not None and device_type.handles(id):
            member = device_type.cls[id]
            device_type.dispatch(config.registry.ctx, member, m.Config(member, parsed), {})
            return
    raise Exception(f"Unknown device: {id}")


# --- State manager ---

ORC_SYSTEM_SNAPSHOT = "ORC_SYSTEM_SNAPSHOT"
_RUN_DISPLAY = {ORC_SYSTEM_SNAPSHOT: "Restore Snapshot"}


class SnapshotManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.snapshots: dict[str, m.SnapShot] = {}

    @synchronized
    def replace_config(self, name: str, target_config: m.Configs, end: datetime, label: str) -> None:

        if name not in self.snapshots:
            self.snapshots[name] = m.SnapShot(capture_lights(), end, label)
            # captured light states are always enum members, not the class/set arm
            routine_items = self.snapshots[name].routine.items
            items = ", ".join(f"`{c.what.name}`={c.state}" for c in routine_items if c.state != m.OFF)  # type: ignore[union-attr]
            log(m.LogSource.SYSTEM, Log.SNAPSHOT_TAKEN.format(name=label, end=end, items=items or Log.SNAPSHOT_ALL_OFF))

        dispatch(target_config, force=True)

    @staticmethod
    def _live(snapshot: m.SnapShot | None) -> bool:
        return bool(snapshot and local_now() <= snapshot.end)

    @synchronized
    def get(self, name: str) -> m.SnapShot | None:
        snapshot = self.snapshots.pop(name, None)
        return snapshot if self._live(snapshot) else None

    @synchronized
    def active(self, name: str) -> bool:
        return self._live(self.snapshots.get(name))

    @synchronized
    def resume(self, name: str, target_config: m.Configs) -> None:
        snapshot = self.get(name)

        if snapshot:
            routine = snapshot.routine
            log(m.LogSource.SYSTEM, Log.SNAPSHOT_RESTORED.format(name=snapshot.label))
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
        snapshot = self.snapshots.get(ORC_SYSTEM_SNAPSHOT)
        if snapshot is None:
            return False
        elif rule.trigger == m.Trigger.SYSTEM:
            self.update_snapshot(ORC_SYSTEM_SNAPSHOT, rule)
        elif not self._live(snapshot):
            self.snapshots.pop(ORC_SYSTEM_SNAPSHOT, None)
        else:
            what = [rule.what] if isinstance(rule.what, Enum) else rule.what
            kinds = ", ".join(f"`{kind}`" for kind in sorted({type(e).__name__ for e in what}))
            log(m.LogSource.SYSTEM, Log.RULE_SUPPRESSED.format(kinds=kinds))
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
    return m.THEME_DAY_OFF if config.providers.holiday.market_holiday(today) else m.THEME_WORK_DAY


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
        log(m.LogSource.MANUAL, Log.THEME_OVERRIDE_CLEARED)
        clear_theme_override()
    else:
        assert start is not None and end is not None  # a named theme override always carries a start/end window
        set_theme_override(name, start, end)
        log(m.LogSource.MANUAL, Log.THEME_OVERRIDE_SET.format(name=name, start=start, end=end))
    after = calculate_theme(today)
    rebuild_jobs(ctx)
    if before != after:
        replay_day(now)


def check_presence(silent: bool = False) -> set[str]:
    pairs = [(name, host, mac) for name, entries in config.people.items() for host, mac in entries]
    if not pairs:
        return present_names()
    before = present_names()
    present, errors = net.scan_presence(pairs)
    for name, exc in errors:
        log(m.LogSource.SYSTEM, Log.PRESENCE_PING_FAILED.format(name=name, exc=exc))
    mark_present(present, local_now())
    after = present_names()

    if not silent:
        if detected := sorted(after - before):
            log(m.LogSource.SYSTEM, Log.PRESENCE_DETECTED.format(name=", ".join(detected)))
        if lost := sorted(before - after):
            log(m.LogSource.SYSTEM, Log.PRESENCE_LOST.format(name=", ".join(lost)))
    return after


def get_schedule() -> list[tuple[datetime, m.Routine]]:
    result: list[tuple[datetime, m.Routine]] = []
    for x in range(2):
        now = local_now() + timedelta(days=x)
        today = now.date()

        local_midnight = datetime(today.year, today.month, today.day, tzinfo=config.settings.tz)
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
                # e.when is a datetime.time here (normalized in __post_init__), not the SUNRISE/SUNSET str
                time = now.replace(hour=e.when.hour, minute=e.when.minute, second=0)
            if time is None:
                continue
            result.append((time.astimezone(config.settings.tz), e))
    return result


def next_iot_job(scheduler: BaseScheduler, present_names: set[str]) -> Job | None:
    jobs = sorted(jobs_by_type(scheduler, m.IotJob), key=lambda e: e.trigger.run_date)
    return next(
        (
            j
            for j in jobs
            if j.next_run_time
            and not any(cfg.trigger == m.Trigger.SYSTEM for cfg in j.args[0].rule.items)
            and matching_items(j.args[0].rule, j.next_run_time, present_names)
        ),
        None,
    )


@requires_ctx
def run_iot_job(job: m.IotJob, ctx: m.AppContext) -> None:
    run_schedule_routine(job.rule, log(m.LogSource.ROUTINE, f"`{job.rule.name}`"))


def run_schedule_routine(rule: m.Routine, entry: m.LogEntry, force: bool = False) -> None:
    now = local_now()
    pnames = present_names()
    if not (matched := matching_items(rule, now, pnames)):
        if not pnames:
            detail = "nobody home"
        else:
            unmet = sorted({c.trigger for c in rule.items if c.trigger not in (None, m.Trigger.SYSTEM, m.Trigger.ANYONE)})
            detail = ", ".join(unmet) if unmet else "no conditions met"
        entry.action += f" — {Log.RULE_SKIPPED.format(detail=detail)}"
        return
    elif weather_triggers := {c.trigger for c in matched if c.trigger in _WEATHER_TRIGGERS}:
        entry.action += f" (weather: {', '.join(sorted(weather_triggers))})"
    dispatch(_squish_matched(rule, matched, entry), force=force, entry=entry)


def _squish_matched(rule: m.Routine, matched: Sequence[m.Config], entry: m.LogEntry) -> m.Configs:
    def log_conflict(what: m.DeviceEnum, states: list[Any]) -> None:
        entry.add(entry.source, Log.CONFLICTING_ARMS.format(device=what.name, states=", ".join(map(str, states))))

    return m.squish_configs(replace(rule, items=matched), on_conflict=log_conflict)


def rebuild_jobs(ctx: m.AppContext) -> None:
    ctx.scheduler.remove_all_jobs()
    setup_scheduler(ctx)


def setup_scheduler(ctx: m.AppContext) -> None:
    if not jobs_by_type(ctx.scheduler, m.IotJob):
        rebuild_iot_schedule(ctx=ctx)
    for job_id, func, crontab, name in (
        ("iot-cron", rebuild_iot_schedule, "10 0 * * *", "Iot Cron"),
        ("presence-cron", _check_presence_job, "5 * * * *", "Presence Cron"),
    ):
        schedule_cron(ctx.scheduler, func, crontab, replace_existing=True, id=job_id, name=name, jobstore=JOBSTORE_MEMORY)


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
    return tuple(
        c
        for c in rule.items
        if c.trigger in _WEATHER_TRIGGERS
        and c.trigger in config.providers.weather.fetch_weather(now, config.settings.lat, config.settings.long)
    )


def matching_items(rule: m.Routine, now: datetime, pnames: set[str]) -> Sequence[m.Config]:
    system = tuple(c for c in rule.items if not c.trigger or c.trigger == m.Trigger.SYSTEM)
    presence = matched_presence(rule, pnames)
    weather = matched_weather(rule, now) if pnames else ()
    return system + presence + weather


@requires_ctx
def rebuild_iot_schedule(ctx: m.AppContext) -> None:
    now = local_now()
    for run_at, rule in get_schedule():
        if now <= run_at:
            schedule_once(
                ctx.scheduler,
                run_iot_job,
                run_at,
                args=[m.IotJob(rule)],
                name=rule.name,
                id=f"iot-{rule.name}-{run_at.date().isoformat()}",
                replace_existing=True,
            )


def replay_day(now: datetime) -> None:
    jobs = sorted(get_schedule(), key=lambda x: x[0])
    present = present_names()
    # replace() keeps Routine type; squish_configs only reads .items, which Routine and Configs share
    configs = (replace(cfg, items=matching_items(cfg, now, present)) for (when, cfg) in jobs if when <= now and not cfg.skip_replay)
    dispatch(m.squish_configs(*configs), force=True)


@requires_ctx
def _check_presence_job(ctx: m.AppContext) -> set[str]:
    return check_presence()
