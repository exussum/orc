import contextlib
import math
import time
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor as Pool
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from functools import cache, lru_cache, partial
from importlib import resources  # nosemgrep: python37-compatibility-importlib2
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import quote

from apscheduler.job import Job
from skyfield import almanac
from skyfield.api import load, load_file, wgs84

from orc import config, plugins
from orc import model as m
from orc.collections import LockedDeque
from orc.dal import net, push, sqlite
from orc.dal.sqlite import (
    connection,  # noqa: F401
    init_db,  # noqa: F401
    update_avg,
)
from orc.dal.sqlite import delete_theme_override as clear_theme_override
from orc.dal.sqlite import fetch_durations as _fetch_durations
from orc.decorators import mappable, requires_ctx
from orc.kernel import cast, engine
from orc.kernel.declarations import Declarations
from orc.locale import Log

DEFAULT_ALERT_PATH = str((Path(__file__).parent / "static" / "alert.wav").resolve())
ORC_SYSTEM_SNAPSHOT = m.ORC_SYSTEM_SNAPSHOT

_ROLLUP_WINDOW = timedelta(seconds=5)
_WEATHER_TRIGGERS: frozenset[str] = frozenset(wc.value for wc in m.WeatherCondition)
_RUN_DISPLAY = {ORC_SYSTEM_SNAPSHOT: "Restore Snapshot"}

_ctx: m.AppContext | None = None
_ACTIVITY_LOG: LockedDeque[m.LogEntry] = LockedDeque(maxlen=200)

last_seen = net.presence.seen
mark_present = net.presence.mark
resume_presence = net.presence.resume


def set_ctx(ctx: m.AppContext) -> None:
    global _ctx
    _ctx = ctx


def duration_stats() -> dict[str, tuple[int, float]]:
    """name -> (samples, average seconds); job names and command topics alike."""
    return {name: (samples, avg) for name, samples, avg in _fetch_durations()}


@mappable
def fetch_durations() -> list[tuple[str, int]]:
    return [(name, math.ceil(avg)) for name, (_, avg) in duration_stats().items()]


def action_delays() -> dict[str, timedelta]:
    ad_hoc = {id: routine.delay for id, routine in config.ad_hoc_routines.items()}
    plugins = {p.name: p.delay for p in config.plugins if isinstance(p, m.CallablePlugin)}
    return ad_hoc | plugins


@contextlib.contextmanager
def record_duration(name: str) -> Iterator[None]:
    start = time.perf_counter()
    yield
    update_avg(name, time.perf_counter() - start)


# --- Utilities ---


def set_job_paused(id: str, paused: bool) -> bool:
    return _scheduler().set_paused(id, paused)


@mappable
def fetch_jobs_by_type(type: type) -> list[Job]:
    return _scheduler().matching(type)


def local_now() -> datetime:
    return datetime.now(tz=config.settings.tz)


def _notify(
    source: m.LogSourceEnum,
    action: str,
    trigger: m.Trigger,
    tag: tuple[str, ...],
    subscriptions: tuple[m.PushSubscription, ...] | None = None,
) -> None:
    title = Log.PUSH_TITLE.format(source=source.capitalize(), date=f"{local_now():%m/%d}")
    _schedule_push(title, action.replace("`", ""), ":".join(tag), trigger, subscriptions)


def push_public_key() -> str:
    return config.providers.push.public_key()


def subscribe_push(subscription: m.PushSubscription, *, greet: bool = False) -> None:
    sqlite.insert_push_subscription(subscription)
    if greet:
        _notify(m.LogSource.SYSTEM, Log.PUSH_GREETING, m.Manual("notify"), ("greeting",), (subscription,))


def unsubscribe_push(endpoint: str) -> None:
    sqlite.delete_push_subscription(endpoint)


def log(source: m.LogSourceEnum, action: str, trigger: m.Trigger, *, notification_tag: tuple[str, ...] = ()) -> m.LogEntry:
    now = local_now()
    entries = _ACTIVITY_LOG.snapshot()
    recent = [e for e in entries if now - (e.children or [e])[-1].timestamp < _ROLLUP_WINDOW]
    # is: a continuation of the event that started the entry, however late (the caller holds entry.trigger).
    # ==: a separate event with an equal trigger, rolled up only within the window.
    matching = next((e for e in entries if e.trigger is trigger or (e in recent and e.trigger == trigger)), None)
    parent = matching or next((e for e in recent if e.answer(trigger)), None)
    if parent:
        parent.add(source, action, notified=bool(notification_tag))
        parent.notified = parent.notified or bool(notification_tag)
    else:
        parent = m.LogEntry(now, source, action, trigger, notified=bool(notification_tag))
        _ACTIVITY_LOG.appendleft(parent)
    if notification_tag:
        _notify(source, action, parent.trigger, notification_tag)
    return parent


def log_entries() -> list[m.LogEntry]:
    return _ACTIVITY_LOG.snapshot()


# --- Device control ---


def add_listener(fn: m.Listener) -> None:
    config.providers.mqtt.add_listener(fn)


def add_button_listener(fn: m.ButtonListener) -> None:
    config.providers.mqtt.add_button_listener(fn)


def add_external_listener(fn: m.Listener) -> None:
    config.providers.mqtt.add_external_listener(fn)


def device_states() -> list[m.DeviceState]:
    return config.providers.mqtt.snapshot()


def device_state(target: str) -> m.DeviceState | None:
    return next((s for s in device_states() if str(s.id) == target or s.name == target), None)


@mappable
def capture_lights() -> m.Commands:
    return config.providers.mqtt.fetch_light_states(tuple(config.devices.Light))


@mappable
def capture_sounds() -> tuple[m.SoundState, ...]:
    devices: tuple[m.DeviceEnum, ...] = (*config.devices.Chromecast, *config.devices.USB)
    if not devices:
        return ()

    def fetch(w: m.DeviceEnum) -> m.SoundState:
        provider = config.providers.chromecast if isinstance(w, config.devices.Chromecast) else config.providers.audio
        return provider.fetch_state(w)

    with Pool(max_workers=len(devices)) as ex:
        return tuple(ex.map(fetch, devices))


def capture_acs() -> tuple[m.AcStatus, ...]:
    return tuple(m.AcStatus(w, ac_state(w), ac_temperature(w)) for w in config.devices.AC)


def capture_sensors() -> list[m.DeviceStatus]:
    found = {s.id: s for s in device_states()}
    return [
        m.DeviceStatus(
            name=found[sensor.value].name if sensor.value in found else sensor.label or sensor.name,
            details=found[sensor.value].attributes if sensor.value in found else {},
        )
        for sensor in config.devices.Sensor
    ]


def add_state_provider(title: str, provider: Callable[[], Any]) -> None:
    config.registry.state_providers[title] = provider


def declare_core(declarations: Declarations) -> None:
    declarations.declare_dispatch("Light", _dispatch_light)
    declarations.declare_dispatch("Chromecast", _dispatch_chromecast)
    declarations.declare_dispatch("USB", _dispatch_usb)
    declarations.declare_dispatch("AC", _dispatch_ac)
    declarations.controllable_devices.append("Light")
    declarations.controllable_devices.append("Chromecast")
    declarations.controllable_devices.append("AC")
    declarations.controllable_devices.append("USB")


class RunAction(NamedTuple):
    effect: Callable[[m.LogEntry], object]
    delay: timedelta = timedelta()


def run_action(
    ctx: m.AppContext,
    id: str,
    trigger: m.Trigger,
    *,
    source: m.LogSourceEnum,
    device: str | None = None,
    skip_delay: bool = False,
) -> bool:
    if id == ORC_SYSTEM_SNAPSHOT:
        action = RunAction(lambda entry: ctx.engine.restore_scene(ctx, ORC_SYSTEM_SNAPSHOT, config.default_config.commands, entry))
    elif (plugin := config.plugin(id)) is not None:
        action = RunAction(lambda entry: plugins.execute_plugin(ctx, plugin, device, entry=entry), plugin.delay)
    elif id in config.schedule_routines:
        action = RunAction(lambda entry: run_schedule_routine(config.schedule_routines[id], entry, set(config.people), force=True))
    elif id in config.ad_hoc_routines:
        routine = config.ad_hoc_routines[id]
        if isinstance(trigger, m.Button) and routine.snapshot and not ctx.engine.snapshot_active(ORC_SYSTEM_SNAPSHOT):
            end = local_now() + routine.snapshot
            action = RunAction(lambda entry: ctx.engine.override_scene(ctx, ORC_SYSTEM_SNAPSHOT, routine.commands, end, id, entry))
        else:
            base = config.reset_config.commands if routine.reset else ()
            commands = (*base, *routine.commands)
            action = RunAction(lambda entry: dispatch(commands, force=True, entry=entry), routine.delay)
    else:
        return False

    display = f"`{_RUN_DISPLAY.get(id, id)}`"
    with record_duration(id):
        if action.delay and not skip_delay:
            when = local_now() + action.delay
            log(source, Log.TASK_QUEUED.format(id=id, when=when), trigger)
            run = requires_ctx(lambda ctx: action.effect(log(source, display, trigger)))
            _scheduler().once(run, when, id=f"run-{id}")
        else:
            action.effect(log(source, display, trigger))
    return True


def run_room(id: str, state: str | None, trigger: m.Trigger) -> None:
    room = config.rooms[id]
    if state == m.ON:
        commands = room.commands
    elif state == m.OFF:
        commands = m.squish(room.commands, state_override=m.OFF)
    elif state == m.FOLLOW:
        others = (c for group in config.rooms.values() for c in group.commands)
        commands = (*m.squish(others, state_override=m.OFF), *room.commands)
    else:
        raise ValueError(f"Unknown room state: {state}")
    entry = log(m.LogSource.MANUAL, Log.ROOM_SET.format(id=id, state=state), trigger)
    with record_duration(id):
        dispatch(commands, force=True, entry=entry)


type _Job = tuple[Callable[..., None], m.DeviceEnum, engine.Command[Any]]


def dispatch(commands: m.Commands, force: bool = False, *, entry: m.LogEntry) -> None:
    assert _ctx is not None
    commands = m.squish(commands)
    always = engine.Action(commands)
    survived = set(_ctx.engine.evaluate((always,), read=clock_reader, force=force)[0].commands)

    stream: dict[Any, tuple[str, str]] = {}
    todo: list[_Job] = []
    for command in commands:
        w = command.channel.one()
        if command not in survived:
            entry.add(entry.source, Log.RULE_SUPPRESSED.format(kinds=f"`{w.kind}`"))
        elif w in config.virtual_devices:
            entry.add(entry.source, Log.VIRTUAL_DEVICE_SKIPPED.format(device=w.name))
        elif (dispatch_handler := config.registry.dispatch_handlers.get(w.kind)) is None:
            raise LookupError(f"no dispatch handler for `{w.kind}`")
        else:
            todo.append((dispatch_handler, w, command))

    entry.requests += tuple(m.Request(str(w.value), command.value) for _, w, command in todo)
    with Pool(max_workers=max(1, len(todo))) as ex:
        list(ex.map(partial(_dispatch_one, stream=stream, entry=entry), todo))


def _dispatch_one(job: _Job, *, stream: dict[Any, tuple[str, str]], entry: m.LogEntry) -> None:
    handler, w, command = job
    try:
        handler(_ctx, w, command, stream)
    except Exception as exc:
        msg = Log.DISPATCH_FAILED.format(device=w.name, exc=exc)
        entry.add(entry.source, msg)
        _notify(entry.source, msg, entry.trigger, ("dispatch",))
        try:
            config.providers.audio.speak(config.settings.attention_device, m.Speak(msg))
        except Exception:
            pass


def alert(severity: m.Alarm, *, text: str | None = None, path: str | None = None, entry: m.LogEntry) -> None:
    if (text is None) == (path is None):
        raise ValueError("alert() requires exactly one of text or path")
    device = _alarm_device(severity)
    if path is not None and not isinstance(device, config.devices.USB):
        raise ValueError(f"{device!r}: alert() takes a local file path, which only USB devices can play")

    if severity is m.Alarm.EMERGENCY:
        dispatch(config.routines[config.settings.emergency_routine].commands, force=True, entry=entry)
        if text is not None:
            video_url = m.AlertVideo(f"{config.settings.base_url}/api/alert.mp4?text={quote(text)}")
            dispatch((engine.Command(m.Devices(config.devices.Chromecast), video_url),), force=True, entry=entry)
            if not isinstance(device, config.devices.Chromecast):
                dispatch((engine.Command(m.Devices(device), m.Speak(text)),), force=True, entry=entry)
    elif text is not None:
        dispatch((engine.Command(m.Devices(device), m.Speak(text)),), force=True, entry=entry)
    else:
        assert path is not None
        dispatch((engine.Command(m.Devices(device), path),), force=True, entry=entry)


def reboot_hubitat() -> None:
    config.providers.hubitat.reboot()


def fetch_retry_stats() -> tuple[m.RetryStats, ...]:
    return config.providers.hubitat.fetch_retry_stats()


def tv_toggle(bl_device: m.DeviceEnum) -> None:
    config.providers.blaster.tv_toggle(bl_device, config.settings.broadlink_codes)


def set_ac(backend: m.AcService) -> None:
    config.registry.ac = backend


def ac_command(device: m.DeviceEnum, state: str | None, mode: str | None = None, fan: str | None = None, temp: int | None = None) -> None:
    if config.registry.ac is None:
        raise RuntimeError("no AC backend registered; enable an AC plugin (e.g. orc_extras.lg_ac)")
    config.registry.ac.command(device, state, mode, fan, temp)


def ac_state(device: m.DeviceEnum) -> m.AcState | None:
    return config.registry.ac.state(device) if config.registry.ac else None


def ac_temperature(device: m.DeviceEnum) -> int | None:
    return config.registry.ac.temperature(device) if config.registry.ac else None


def device_command(id: str, state: str | None, entry: m.LogEntry) -> bool:
    # Find the device across dispatch-handled types and run its registered handler
    # directly (no snapshot interception), so plugin device types work without core
    # knowing them. state is an int level (brightness/volume), an ON/OFF/STOP string,
    # or a mode:fan:temp AC command.
    if state and state.isdigit():
        parsed: Any = int(state)
    elif state and ":" in state:
        parsed = cast.state(state)
    else:
        parsed = state
    for name, cls in config.devices.items():
        dispatch_handler = config.registry.dispatch_handlers.get(name)
        if dispatch_handler is not None and id in cls.__members__:
            member = cls[id]
            entry.requests += (m.Request(str(member.value), parsed),)
            dispatch_handler(_ctx, member, engine.Command(m.Devices(member), parsed), {})
            return True
    return False


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
    return base_theme(today)


def is_working_day(today: date) -> bool:
    """Whether the day's theme is the working one — asked by plugins so a theme name stays out of them."""
    return calculate_theme(today) == m.THEME_WORK_DAY


def base_theme(today: date) -> str:
    if today.weekday() in (5, 6):
        return m.THEME_DAY_OFF
    return m.THEME_DAY_OFF if config.providers.holiday.market_holiday(today) else m.THEME_WORK_DAY


def set_theme_override(name: str, start: date, end: date) -> None:
    sqlite.insert_theme_override(m.ThemeOverride(name, start, end))


def present_names() -> set[str]:
    return net.presence.present(local_now() - timedelta(hours=config.settings.presence_hours))


def expire_presence(names: list[str], trigger: m.Trigger, force: bool = False) -> None:
    net.presence.forget(names, trigger, before=None if force else local_now())


def delete_all_presence(trigger: m.Trigger) -> None:
    expire_presence(list(net.presence.seen()), trigger)


def pause_presence() -> None:
    net.presence.pause(local_now())


def start_ble_listener() -> None:
    reported: set[str] = set()

    def report(trigger: m.Trigger) -> None:
        nonlocal reported
        present = present_names()
        if detected := sorted(present - reported):
            log(m.LogSource.SYSTEM, Log.PRESENCE_DETECTED.format(name=", ".join(detected)), trigger)
        if lost := sorted(reported - present):
            log(m.LogSource.SYSTEM, Log.PRESENCE_LOST.format(name=", ".join(lost)), trigger)
        reported = present

    net.presence.start(config.ble_tags, config.settings.tz, report, clocks=sqlite.fetch_tag_clocks(), on_clock=sqlite.upsert_tag_clock)


def schedule_presence_check(trigger: m.Trigger) -> None:
    if config.people:
        _scheduler().now(_check_presence_job, trigger, name="Presence Boot Check", skip_if_late=True)


def apply_theme_change(ctx: m.AppContext, name: str, start: date | None, end: date | None, trigger: m.Trigger) -> None:
    if not name:
        log(m.LogSource.MANUAL, Log.THEME_OVERRIDE_CLEARED, trigger)
        clear_theme_override()
    else:
        assert start is not None and end is not None  # a named theme override always carries a start/end window
        set_theme_override(name, start, end)
        log(m.LogSource.MANUAL, Log.THEME_OVERRIDE_SET.format(name=name, start=start, end=end), trigger)
    rebuild_jobs(ctx)


def check_presence(trigger: m.Trigger, source: m.LogSourceEnum = m.LogSource.SYSTEM, *, probe: bool = False) -> set[str]:
    pairs = [(name, host, mac) for name, entries in config.people.items() for host, mac in entries]
    if not pairs and not config.ble_tags:
        return present_names()
    present = present_names()
    stale = set(net.presence.seen()) - present
    tags = set(config.ble_tags)
    net.presence.probe(tags - present if probe else stale & tags, trigger)
    if lost := stale - present_names():
        expire_presence(sorted(lost), trigger)
    present, errors = net.scan_presence(pairs)
    for name, exc in errors:
        msg = Log.PRESENCE_SCAN_FAILED.format(name=name, exc=exc)
        entry = log(source, msg, trigger, notification_tag=("presence",))
        alert(m.Alarm.ATTENTION, text=msg, entry=entry)
    mark_present(present, local_now(), trigger)
    return present_names()


def get_schedule() -> list[tuple[datetime, m.Routine]]:
    result: list[tuple[datetime, m.Routine]] = []
    for x in range(2):
        now = local_now() + timedelta(days=x)
        today = now.date()

        local_midnight = datetime(today.year, today.month, today.day, tzinfo=config.settings.tz)
        sunrise, sunset = _sun_times(local_midnight, config.settings.lat, config.settings.long, config.settings.sunset_lead_hours)

        for when, routine in _scheduled_theme(today).entries:
            run_at = _entry_time(when, sunrise, sunset, now)
            if run_at is not None:
                result.append((run_at, routine))
    return result


def _scheduled_theme(today: date) -> m.Theme:
    if override := active_theme_override(today):
        theme = config.themes.get(override.name)
    else:
        theme = config.themes.get(today.strftime("%A").lower()) or config.themes.get(base_theme(today))
    assert theme is not None
    return theme


def _entry_time(when: dt_time | str, sunrise: datetime | None, sunset: datetime | None, now: datetime) -> datetime | None:
    if when == m.SUNRISE:
        return sunrise
    elif when == m.SUNSET:
        return sunset
    clock = cast.instance(when, dt_time)
    return now.replace(hour=clock.hour, minute=clock.minute, second=0)


@lru_cache(maxsize=1)
def _almanac(lat: float, long: float) -> tuple[Any, Any]:
    ephemeris = load_file(str(resources.files("orc_data") / "de421.bsp"))
    return load.timescale(), almanac.dark_twilight_day(ephemeris, wgs84.latlon(lat, long))


def _sun_times(midnight: datetime, lat: float, long: float, sunset_lead_hours: int) -> tuple[datetime | None, datetime | None]:
    ts, twilight = _almanac(lat, long)
    start, end = ts.from_datetime(midnight), ts.from_datetime(midnight + timedelta(days=1))
    prev, sunrise, sunset = int(twilight(start).item()), None, None
    for t, curr in zip(*almanac.find_discrete(start, end, twilight), strict=True):
        curr = int(curr)
        if (prev, curr) == (3, 4):
            sunrise = t.astimezone(midnight.tzinfo)
        elif (prev, curr) == (4, 3):
            sunset = t.astimezone(midnight.tzinfo) - timedelta(hours=sunset_lead_hours)
        prev = curr
    return sunrise, sunset


def next_iot_job(present_names: set[str]) -> Job | None:
    jobs = sorted(fetch_jobs_by_type(m.IotJob), key=lambda e: e.trigger.run_date)
    return next(
        (
            j
            for j in jobs
            if j.next_run_time
            and not any(step.command.tag == m.Tag.SYSTEM for step in j.args[0].rule.steps)
            and matching_items(j.args[0].rule, j.next_run_time, present_names)
        ),
        None,
    )


@requires_ctx
def run_iot_job(job: m.IotJob, ctx: m.AppContext) -> None:
    run_schedule_routine(job.rule, log(m.LogSource.ROUTINE, f"`{job.rule.name}`", m.Scheduled(job.rule.name)), present_names())


def squish(commands: Iterable[m.DeviceCommand], entry: m.LogEntry) -> m.Commands:
    def log_conflict(what: m.DeviceEnum, states: list[Any]) -> None:
        entry.add(entry.source, Log.CONFLICTING_ARMS.format(device=what.name, states=", ".join(map(str, states))))

    return m.squish(commands, on_conflict=log_conflict)


def run_schedule_routine(rule: m.Routine, entry: m.LogEntry, pnames: set[str], force: bool = False) -> None:
    now = local_now()
    if not (matched := matching_items(rule, now, pnames)):
        if not pnames:
            detail = "nobody home"
        else:
            unmet = sorted({step.command.tag for step in rule.steps if step.command.tag not in (None, m.Tag.SYSTEM, m.Tag.ANYONE)})
            detail = ", ".join(unmet) if unmet else "no conditions met"
        entry.action += f" — {Log.RULE_SKIPPED.format(detail=detail)}"
        return
    elif weather_triggers := {c.tag for c in matched if c.tag in _WEATHER_TRIGGERS}:
        entry.action += f" (weather: {', '.join(sorted(weather_triggers))})"
    dispatch(squish(matched, entry), force=force, entry=entry)


def rebuild_jobs(ctx: m.AppContext) -> None:
    ctx.scheduler.rebuild()
    setup_scheduler(ctx)


def setup_scheduler(ctx: m.AppContext) -> None:
    if not fetch_jobs_by_type(m.IotJob):
        rebuild_iot_schedule(ctx=ctx)
    for job_id, func, crontab, name in (
        ("iot-cron", rebuild_iot_schedule, "10 0 * * *", "Iot Cron"),
        ("presence-cron", _presence_cron_job, "5 * * * *", "Presence Cron"),
        ("jobs-cleanup-cron", _cleanup_stale_jobs, "15 0 * * *", "Jobs Cleanup Cron"),
    ):
        ctx.scheduler.cron(func, crontab, id=job_id, name=name)


def _presence(rule: m.Routine) -> m.Routine:
    return rule.where(lambda command: command.tag not in (None, m.Tag.SYSTEM, *_WEATHER_TRIGGERS))


def has_presence(rule: m.Routine) -> bool:
    return bool(_presence(rule).steps)


def is_absent(rule: m.Routine, present_names: set[str]) -> bool:
    presence = _presence(rule)
    return bool(presence.steps) and not presence.holds(world_reader(present_names))


def weather_active(rule: m.Routine, now: datetime) -> bool:
    return rule.where(lambda command: command.tag in _WEATHER_TRIGGERS).holds(lambda _channel: _fetch_weather(now))


def matching_items(rule: m.Routine, now: datetime, pnames: set[str]) -> m.Commands:
    assert _ctx is not None
    return _ctx.engine.evaluate((rule,), read=world_reader(pnames, now), force=True)[0].commands


def world_reader(present: set[str] | None = None, now: datetime | None = None) -> engine.Read:
    pnames = present_names() if present is None else present
    when = local_now() if now is None else now

    @cache
    def read(channel: engine.Channel) -> engine.Value:
        match channel:
            case m.PersonChannel(name):
                return name in pnames
            case m.AnyoneChannel():
                return bool(pnames)
            case m.WeatherChannel():
                return _fetch_weather(when) if pnames else frozenset()
            case _:
                raise KeyError(channel)

    return read


def clock_reader(channel: engine.Channel) -> engine.Value:
    if isinstance(channel, engine.Clock):
        return local_now()
    raise KeyError(channel)


def runtime() -> engine.Runtime:
    return engine.Runtime(clock_reader, bypass=m.Tag.SYSTEM, override_key=ORC_SYSTEM_SNAPSHOT)


def _fetch_weather(now: datetime) -> frozenset[m.WeatherCondition]:
    return config.providers.weather.fetch_weather(now, config.settings.lat, config.settings.long)


@requires_ctx
def rebuild_iot_schedule(ctx: m.AppContext) -> None:
    now = local_now()
    for run_at, rule in get_schedule():
        if now <= run_at:
            ctx.scheduler.once(
                run_iot_job, run_at, m.IotJob(rule), name=rule.name, id=f"iot-{rule.name}-{run_at.date().isoformat()}", persist=True
            )


def replay_day(now: datetime, entry: m.LogEntry) -> None:
    jobs = sorted(get_schedule(), key=lambda x: x[0])
    present = present_names()
    matched = [c for (when, cfg) in jobs if when <= now and m.SKIP_REPLAY_TAG not in cfg.tags for c in matching_items(cfg, now, present)]
    dispatch(tuple(matched), force=True, entry=entry)


# --- Private helpers ---


def _scheduler() -> m.Scheduler:
    assert _ctx is not None
    return _ctx.scheduler


def _dispatch_light(ctx: m.AppContext, w: m.DeviceEnum, command: engine.Command[Any], stream: dict[Any, tuple[str, str]]) -> None:
    if isinstance(command.value, int):
        config.providers.mqtt.publish_light(w, brightness=command.value)
    else:
        config.providers.mqtt.publish_light(w, on=command.value == m.ON)


def _dispatch_chromecast(ctx: m.AppContext, w: m.DeviceEnum, command: engine.Command[Any], stream: dict[Any, tuple[str, str]]) -> None:
    if isinstance(command.value, int):
        config.providers.chromecast.set_volume(w, command.value)
    elif isinstance(command.value, m.Speak):
        config.providers.chromecast.speak(w, command.value)
    elif isinstance(command.value, m.AlertVideo):
        config.providers.chromecast.play(w, command.value, "Alert")
    elif isinstance(command.value, m.YouTubeId):
        if command.value not in stream:
            stream[command.value] = config.providers.chromecast.fetch_youtube_stream_metadata(command.value)
        url, title = stream[command.value]
        config.providers.chromecast.play(w, m.Stream(url), title)
    elif command.value == m.STOP:
        config.providers.chromecast.stop(w)
    elif command.value == m.PAUSE:
        config.providers.chromecast.pause(w)
    elif command.value == m.RESUME:
        config.providers.chromecast.resume(w)
    else:
        raise ValueError(f"Unsupported Chromecast state: {command.value!r}")


def _dispatch_usb(ctx: m.AppContext, w: m.DeviceEnum, command: engine.Command[Any], stream: dict[Any, tuple[str, str]]) -> None:
    if isinstance(command.value, int):
        config.providers.audio.set_volume(w, command.value)
    elif isinstance(command.value, m.Speak):
        config.providers.audio.speak(w, command.value)
    elif command.value in (m.ON, m.OFF, m.STOP, m.PAUSE, m.RESUME):
        raise ValueError(f"USB devices don't support state {command.value!r}")
    else:
        config.providers.audio.alert(w, command.value)


def _dispatch_ac(ctx: m.AppContext, w: m.DeviceEnum, command: engine.Command[Any], stream: dict[Any, tuple[str, str]]) -> None:
    if isinstance(command.value, m.AcCommand):
        ac_command(w, m.ON, command.value.mode, command.value.fan, command.value.temp)
    elif command.value in (m.ON, m.OFF):
        ac_command(w, command.value)
    else:
        raise ValueError(f"AC devices don't support state {command.value!r}")


def _schedule_push(title: str, body: str, tag: str, trigger: m.Trigger, subscriptions: tuple[m.PushSubscription, ...] | None) -> None:
    _scheduler().now(_push_job, title, body, tag, trigger, subscriptions, name="Push")


@requires_ctx
def _push_job(
    title: str, body: str, tag: str, trigger: m.Trigger, subscriptions: tuple[m.PushSubscription, ...] | None, *, ctx: m.AppContext
) -> None:
    for subscription in subscriptions or sqlite.fetch_push_subscriptions():
        try:
            config.providers.push.send(subscription, title, body, tag)
        except push.Gone:
            sqlite.delete_push_subscription(subscription.endpoint)
        except Exception as exc:
            log(m.LogSource.SYSTEM, Log.PUSH_FAILED.format(endpoint=subscription.endpoint[-8:], exc=exc), trigger)


def _alarm_device(severity: m.Alarm) -> m.DeviceEnum:
    match severity:
        case m.Alarm.WARNING:
            device = config.settings.warning_device
        case m.Alarm.ATTENTION:
            device = config.settings.attention_device
        case _:
            device = config.settings.emergency_device
    assert device is not None
    return device


@requires_ctx
def _cleanup_stale_jobs(ctx: m.AppContext) -> None:
    ctx.scheduler.delete_stale()


@requires_ctx
def _check_presence_job(trigger: m.Trigger, *, ctx: m.AppContext) -> set[str]:
    return check_presence(trigger)


@requires_ctx
def _presence_cron_job(ctx: m.AppContext) -> set[str]:
    return check_presence(m.Cron("presence"))
