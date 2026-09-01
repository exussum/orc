import contextlib
import io
import math
import subprocess
import tempfile
import threading
import time
import urllib.request
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor as Pool
from dataclasses import replace
from datetime import date, datetime, timedelta
from enum import Enum
from importlib import resources  # nosemgrep: python37-compatibility-importlib2
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from apscheduler.job import Job
from PIL import Image, ImageDraw, ImageFont, ImageText
from skyfield import almanac
from skyfield.api import load, load_file, wgs84

import orc
from orc import config, plugins
from orc import model as m
from orc.dal import net, scheduler, sqlite
from orc.dal.chromecast import MAX_CHARS
from orc.dal.scheduler import fetch_jobs_by_type
from orc.dal.sqlite import (
    connection,  # noqa: F401
    init_db,  # noqa: F401
    update_avg,
)
from orc.dal.sqlite import delete_theme_override as clear_theme_override  # noqa: F401
from orc.dal.sqlite import fetch_durations as _fetch_durations
from orc.dal.sqlite import fetch_presence as last_seen  # noqa: F401
from orc.dal.sqlite import insert_presence as mark_present
from orc.declarations import Declarations
from orc.decorators import (
    requires_ctx,
    synchronized,
    unwrap_rule_container,
)
from orc.locale import Log

JOBSTORE_DEFAULT = "default"
JOBSTORE_MEMORY = "memory"
_PRESENCE_CRON_JOB_ID = "presence-cron"

DEFAULT_ALERT_PATH = str((Path(__file__).parent / "static" / "alert.wav").resolve())
ALERT_IMAGE_SIZE = (1280, 720)
_ALERT_MARGIN = 80
_ALERT_MIN_FONT_SIZE = 24


def _render_alert_image(text: str) -> bytes:
    image = Image.new("RGB", ALERT_IMAGE_SIZE, color=(178, 24, 24))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=128)
    wrapped = ImageText.Text(text, font=font)
    wrapped.wrap(
        ALERT_IMAGE_SIZE[0] - 2 * _ALERT_MARGIN,
        ALERT_IMAGE_SIZE[1] - 2 * _ALERT_MARGIN,
        scaling=("shrink", _ALERT_MIN_FONT_SIZE),
    )
    draw.text((ALERT_IMAGE_SIZE[0] / 2, ALERT_IMAGE_SIZE[1] / 2), wrapped, fill="white", anchor="mm", align="center")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


_ALERT_VIDEO_SECONDS = 300
_ALERT_LOOP_SECONDS = 20
_TTS_SAMPLE_RATE = 24000


def _tts_mp3(text: str) -> bytes:
    url = "https://translate.google.com/translate_tts?" + urlencode({"ie": "UTF-8", "q": text, "tl": "en", "client": "tw-ob"})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:  # nosemgrep
        return resp.read()


def render_alert_video(text: str) -> bytes:
    if len(text) > MAX_CHARS:
        raise ValueError(f"Alert text exceeds {MAX_CHARS} characters: {len(text)}")
    with tempfile.TemporaryDirectory() as d:
        png, mp3, mp4 = Path(d) / "a.png", Path(d) / "a.mp3", Path(d) / "a.mp4"
        png.write_bytes(_render_alert_image(text))
        mp3.write_bytes(_tts_mp3(text))
        # Pad the speech to a fixed period and loop it so the announcement repeats
        # every _ALERT_LOOP_SECONDS with silence between, over the full video.
        loop_samples = _ALERT_LOOP_SECONDS * _TTS_SAMPLE_RATE
        audio = f"[1:a]aresample={_TTS_SAMPLE_RATE},apad=whole_dur={_ALERT_LOOP_SECONDS},aloop=loop=-1:size={loop_samples}[a]"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-loop",
                "1",
                "-i",
                str(png),
                "-i",
                str(mp3),
                "-filter_complex",
                audio,
                "-map",
                "0:v",
                "-map",
                "[a]",
                "-c:v",
                "libx264",
                "-tune",
                "stillimage",
                "-pix_fmt",
                "yuv420p",
                "-r",
                "5",
                "-c:a",
                "aac",
                "-b:a",
                "64k",
                "-t",
                str(_ALERT_VIDEO_SECONDS),
                "-movflags",
                "+faststart",
                str(mp4),
            ],
            check=True,
        )
        return mp4.read_bytes()


_ctx: m.AppContext | None = None


def set_ctx(ctx: m.AppContext) -> None:
    global _ctx
    _ctx = ctx


_PRESENCE_WINDOW = timedelta(hours=9)
_ACTIVITY_LOG: deque[m.LogEntry] = deque(maxlen=200)
_NOTIFICATIONS: deque[m.LogEntry] = deque(maxlen=10)
_WEATHER_TRIGGERS: frozenset[str] = frozenset(wc.value for wc in m.WeatherCondition)

_TIMESCALE = load.timescale()
_EPHEMERIS = load_file(str(resources.files("orc_data") / "de421.bsp"))
_TWILIGHT_FN = almanac.dark_twilight_day(_EPHEMERIS, wgs84.latlon(config.settings.lat, config.settings.long))


def duration_stats() -> dict[str, tuple[int, float]]:
    """name -> (samples, average seconds); job names and command topics alike."""
    return {name: (samples, avg) for name, samples, avg in _fetch_durations()}


def fetch_durations() -> list[tuple[str, int]]:
    return [(name, math.ceil(avg)) for name, (_, avg) in duration_stats().items()]


def action_delay(id: str) -> timedelta:
    if (plugin := config.plugin(id)) is not None:
        return plugin.delay
    if id in config.ad_hoc_routines:
        return config.ad_hoc_routines[id].delay
    return timedelta()


@contextlib.contextmanager
def record_duration(name: str) -> Iterator[None]:
    start = time.perf_counter()
    yield
    update_avg(name, time.perf_counter() - start)


# --- Utilities ---


def toggle_job(id: str) -> bool:
    if not scheduler.job_exists(id):
        return False
    scheduler.resume_job(id) if scheduler.is_paused(id) else scheduler.pause_job(id)
    return True


def local_now() -> datetime:
    return datetime.now(tz=config.settings.tz)


def notify(entry: m.LogEntry) -> m.LogEntry:
    _NOTIFICATIONS.appendleft(entry)
    return entry


def log(source: m.LogSourceEnum, action: str, *, should_notify: bool = False) -> m.LogEntry:
    entry = m.LogEntry(local_now(), source, action)
    _ACTIVITY_LOG.appendleft(entry)
    if should_notify:
        notify(entry)
    return entry


def log_entries() -> list[m.LogEntry]:
    return list(_ACTIVITY_LOG)


# --- Device control ---


def add_listener(fn: m.Listener) -> None:
    config.providers.mqtt.add_listener(fn)


def device_states() -> list[m.DeviceState]:
    return config.providers.mqtt.snapshot()


def capture_lights() -> m.Configs:
    return config.providers.mqtt.fetch_light_states(tuple(orc.Light))


def capture_sounds() -> m.Configs[m.SoundState]:
    devices: tuple[m.DeviceEnum, ...] = (*orc.Chromecast, *orc.USB)
    if not devices:
        return m.Configs()

    def fetch(w: m.DeviceEnum) -> m.SoundState:
        provider = config.providers.chromecast if isinstance(w, orc.Chromecast) else config.providers.audio
        return provider.fetch_state(w)

    with Pool(max_workers=len(devices)) as ex:
        return m.Configs(*ex.map(fetch, devices))


def _dispatch_light(ctx: m.AppContext, w: m.DeviceEnum, rule: m.Config, stream: dict[Any, tuple[str, str]]) -> None:
    if isinstance(rule.state, int):
        config.providers.mqtt.publish_light(w, brightness=rule.state)
    else:
        config.providers.mqtt.publish_light(w, on=rule.state == m.ON)


def _dispatch_chromecast(ctx: m.AppContext, w: m.DeviceEnum, rule: m.Config, stream: dict[Any, tuple[str, str]]) -> None:
    if isinstance(rule.state, int):
        config.providers.chromecast.set_volume(w, rule.state)
    elif isinstance(rule.state, m.Speak):
        config.providers.chromecast.speak(w, rule.state)
    elif isinstance(rule.state, m.AlertVideo):
        config.providers.chromecast.play(w, rule.state, "Alert")
    elif isinstance(rule.state, m.YouTubeId):
        if rule.state not in stream:
            stream[rule.state] = config.providers.chromecast.fetch_youtube_stream_metadata(rule.state)
        url, title = stream[rule.state]
        config.providers.chromecast.play(w, m.Stream(url), title)
    elif rule.state == m.STOP:
        config.providers.chromecast.stop(w)
    elif rule.state == m.PAUSE:
        config.providers.chromecast.pause(w)
    elif rule.state == m.RESUME:
        config.providers.chromecast.resume(w)
    else:
        raise ValueError(f"Unsupported Chromecast state: {rule.state!r}")


def _dispatch_usb(ctx: m.AppContext, w: m.DeviceEnum, rule: m.Config, stream: dict[Any, tuple[str, str]]) -> None:
    if isinstance(rule.state, int):
        config.providers.audio.set_volume(w, rule.state)
    elif isinstance(rule.state, m.Speak):
        config.providers.audio.speak(w, rule.state)
    elif rule.state in (m.ON, m.OFF, m.STOP, m.PAUSE, m.RESUME):
        raise ValueError(f"USB devices don't support state {rule.state!r}")
    else:
        config.providers.audio.alert(w, rule.state)


def add_state_provider(title: str, provider: Callable[[], Any]) -> None:
    config.registry.state_providers[title] = provider


def declare_core(declarations: Declarations) -> None:
    declarations.declare_dispatch("Light", _dispatch_light)
    declarations.declare_dispatch("Chromecast", _dispatch_chromecast)
    declarations.declare_dispatch("USB", _dispatch_usb)
    declarations.controllable_devices.append("Light")
    declarations.controllable_devices.append("Chromecast")
    declarations.controllable_devices.append("AC")
    declarations.controllable_devices.append("USB")


def resolve_run_action(
    ctx: m.AppContext, id: str, *, device: str | None, hub_origin: bool
) -> tuple[Callable[[m.LogEntry], None], timedelta] | None:
    if id == ORC_SYSTEM_SNAPSHOT:
        return lambda entry: ctx.snapshot_manager.resume(ORC_SYSTEM_SNAPSHOT, config.default_config, entry), timedelta()
    elif (plugin := config.plugin(id)) is not None:
        return lambda entry: plugins.execute_plugin(ctx, plugin, device, entry=entry), plugin.delay
    elif id in config.schedule_routines:
        return lambda entry: run_schedule_routine(config.schedule_routines[id], entry, force=True), timedelta()
    elif id in config.ad_hoc_routines:
        routine = config.ad_hoc_routines[id]
        if hub_origin and routine.snapshot and not ctx.snapshot_manager.active(ORC_SYSTEM_SNAPSHOT):
            # Don't stack snapshots
            snap = routine.snapshot
            return (
                lambda entry: ctx.snapshot_manager.replace_config(ORC_SYSTEM_SNAPSHOT, routine, local_now() + snap, id, entry),
                timedelta(),
            )
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
            scheduler.schedule_once(run, when, id=f"run-{id}", replace_existing=True, jobstore=JOBSTORE_MEMORY)
        else:
            run(ctx=ctx)
    return True


def wire_buttons(ctx: m.AppContext) -> None:
    mapping = {(what.value, button, event): action for (what, button, event), action in config.remotes.items()}

    def on_button(device_id: int, button: int, event_type: str) -> None:
        action = mapping.get((device_id, button, event_type))
        if action is not None and not run_action(ctx, action, hub_origin=True):
            msg = Log.BUTTON_ACTION_UNKNOWN.format(id=action)
            entry = log(m.LogSource.SYSTEM, msg, should_notify=True)
            alert(m.Alarm.ATTENTION, text=msg, entry=entry)

    config.providers.mqtt.add_button_listener(on_button)


def wire_external_log() -> None:
    def on_external(device: m.DeviceState, attribute: str, old: Any, new: Any) -> None:
        action = Log.EXTERNAL_CHANGE.format(device=device.name, attribute=attribute, old=old, new=new)
        last = next(iter(_ACTIVITY_LOG), None)
        if not (
            last is not None
            and last.source is m.LogSource.EXTERNAL
            and local_now() - (last.children or [last])[-1].timestamp < timedelta(seconds=5)
        ):
            last = log(m.LogSource.EXTERNAL, Log.EXTERNAL_DETECTED)
        last.add(m.LogSource.EXTERNAL, action)

    config.providers.mqtt.add_external_listener(on_external)


@unwrap_rule_container
def dispatch(rule: m.Config, force: bool = False, *, entry: m.LogEntry) -> None:
    if not force and snapshot_manager.intercepts(rule):
        return
    what = [rule.what] if isinstance(rule.what, Enum) else rule.what
    stream: dict[Any, tuple[str, str]] = {}

    def one(w: m.DeviceEnum) -> None:
        if w in config.virtual_devices:
            entry.add(entry.source, Log.VIRTUAL_DEVICE_SKIPPED.format(device=w.name))
            return

        device_type = config.registry.devices.get(type(w).__name__)
        if device_type is None or device_type.dispatch is None:
            raise Exception("Unknown type")
        try:
            device_type.dispatch(_ctx, w, rule, stream)
        except Exception as exc:
            msg = Log.DISPATCH_FAILED.format(device=w.name, exc=exc)
            notify(entry.add(entry.source, msg))
            try:
                config.providers.audio.speak(config.settings.attention_device, m.Speak(msg))
            except Exception:
                pass  # already recorded via notify() above; don't let error-reporting itself crash the worker

    with Pool(max_workers=max(1, len(what))) as ex:
        list(ex.map(one, what))


_ALARM_SETTINGS = {
    m.Alarm.WARNING: "warning_device",
    m.Alarm.ATTENTION: "attention_device",
    m.Alarm.EMERGENCY: "emergency_device",
}


def alert(severity: m.Alarm, *, text: str | None = None, path: str | None = None, entry: m.LogEntry) -> None:
    if (text is None) == (path is None):
        raise ValueError("alert() requires exactly one of text or path")
    device = getattr(config.settings, _ALARM_SETTINGS[severity])
    if path is not None and not isinstance(device, orc.USB):
        raise ValueError(f"{device!r}: alert() takes a local file path, which only USB devices can play")

    if severity is m.Alarm.EMERGENCY:
        dispatch(config.routines[config.settings.emergency_routine], force=True, entry=entry)
        if text is not None:
            video_url = m.AlertVideo(f"{config.settings.base_url}/api/alert.mp4?text={quote(text)}")
            dispatch(m.Config(orc.Chromecast, video_url), force=True, entry=entry)
            if not isinstance(device, orc.Chromecast):
                dispatch(m.Config(device, m.Speak(text)), force=True, entry=entry)
    elif text is not None:
        dispatch(m.Config(device, m.Speak(text)), force=True, entry=entry)
    else:
        assert path is not None
        dispatch(m.Config(device, path), force=True, entry=entry)


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
            device_type.dispatch(_ctx, member, m.Config(member, parsed), {})
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
    def replace_config(self, name: str, target_config: m.Configs, end: datetime, label: str, entry: m.LogEntry) -> None:

        if name not in self.snapshots:
            self.snapshots[name] = m.SnapShot(capture_lights(), end, label)
            # captured light states are always enum members, not the class/set arm
            routine_items = self.snapshots[name].routine.items
            items = ", ".join(f"`{c.what.name}`={c.state}" for c in routine_items if c.state != m.OFF)  # type: ignore[union-attr]
            entry.add(entry.source, Log.SNAPSHOT_TAKEN.format(name=label, end=end, items=items or Log.SNAPSHOT_ALL_OFF))

        dispatch(target_config, force=True, entry=entry)

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
    def resume(self, name: str, target_config: m.Configs, entry: m.LogEntry) -> None:
        snapshot = self.get(name)

        if snapshot:
            routine = snapshot.routine
            entry.add(entry.source, Log.SNAPSHOT_RESTORED.format(name=snapshot.label))
        else:
            routine = target_config
        dispatch(routine, force=True, entry=entry)

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


def rerun_presence_check(ctx: m.AppContext, source: m.LogSourceEnum = m.LogSource.MANUAL) -> None:
    log(source, Log.PRESENCE_RESCAN)
    delete_all_presence()
    scheduler.invoke_job(_PRESENCE_CRON_JOB_ID, ctx=ctx, source=source)


def apply_theme_change(ctx: m.AppContext, name: str, start: date | None, end: date | None) -> None:
    now = local_now()
    today = now.date()
    before = calculate_theme(today)
    if not name:
        entry = log(m.LogSource.MANUAL, Log.THEME_OVERRIDE_CLEARED)
        clear_theme_override()
    else:
        assert start is not None and end is not None  # a named theme override always carries a start/end window
        set_theme_override(name, start, end)
        entry = log(m.LogSource.MANUAL, Log.THEME_OVERRIDE_SET.format(name=name, start=start, end=end))
    after = calculate_theme(today)
    rebuild_jobs(ctx)
    if before != after:
        replay_day(now, entry)


def check_presence(silent: bool = False, source: m.LogSourceEnum = m.LogSource.SYSTEM) -> set[str]:
    pairs = [(name, host, mac) for name, entries in config.people.items() for host, mac in entries]
    if not pairs:
        return present_names()
    before = present_names()
    present, errors = net.scan_presence(pairs)
    for name, exc in errors:
        msg = Log.PRESENCE_PING_FAILED.format(name=name, exc=exc)
        entry = log(source, msg, should_notify=True)
        alert(m.Alarm.ATTENTION, text=msg, entry=entry)
    mark_present(present, local_now())
    after = present_names()

    if not silent:
        if detected := sorted(after - before):
            log(source, Log.PRESENCE_DETECTED.format(name=", ".join(detected)))
        if lost := sorted(before - after):
            log(source, Log.PRESENCE_LOST.format(name=", ".join(lost)))
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


def next_iot_job(present_names: set[str]) -> Job | None:
    jobs = sorted(fetch_jobs_by_type(m.IotJob), key=lambda e: e.trigger.run_date)
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
    scheduler.remove_all_jobs()
    setup_scheduler(ctx)


def setup_scheduler(ctx: m.AppContext) -> None:
    if not fetch_jobs_by_type(m.IotJob):
        rebuild_iot_schedule(ctx=ctx)
    for job_id, func, crontab, name in (
        ("iot-cron", rebuild_iot_schedule, "10 0 * * *", "Iot Cron"),
        (_PRESENCE_CRON_JOB_ID, _check_presence_job, "5 * * * *", "Presence Cron"),
    ):
        scheduler.schedule_cron(func, crontab, replace_existing=True, id=job_id, name=name, jobstore=JOBSTORE_MEMORY)


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
            scheduler.schedule_once(
                run_iot_job,
                run_at,
                args=[m.IotJob(rule)],
                name=rule.name,
                id=f"iot-{rule.name}-{run_at.date().isoformat()}",
                replace_existing=True,
            )


def replay_day(now: datetime, entry: m.LogEntry) -> None:
    jobs = sorted(get_schedule(), key=lambda x: x[0])
    present = present_names()
    # replace() keeps Routine type; squish_configs only reads .items, which Routine and Configs share
    configs = (replace(cfg, items=matching_items(cfg, now, present)) for (when, cfg) in jobs if when <= now and not cfg.skip_replay)
    dispatch(m.squish_configs(*configs), force=True, entry=entry)


@requires_ctx
def _check_presence_job(ctx: m.AppContext, source: m.LogSourceEnum = m.LogSource.SYSTEM) -> set[str]:
    return check_presence(source=source)
