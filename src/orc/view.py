import random
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from functools import cache, wraps
from itertools import chain, groupby
from pathlib import Path
from typing import Any, NamedTuple, cast

from apscheduler.job import Job
from flask import Blueprint, Flask, abort, render_template, request
from flask import current_app as _current_app
from flask.wrappers import Response
from markupsafe import Markup, escape
from werkzeug.exceptions import NotFound

from orc import alerts, api, config
from orc import model as m
from orc.collections import where
from orc.locale import Log


class OrcFlask(Flask):
    orc: m.AppContext


class PresenceStatus(NamedTuple):
    name: str
    hostnames: list[str]
    last_seen: datetime | None
    present: bool


class HighlightRow(NamedTuple):
    name: str
    start: str
    end: str


class DeviceRow(NamedTuple):
    name: str
    id: str
    type: str
    icon: str
    capabilities: set[str]
    toggle: bool
    level: int
    on: bool
    volume: int
    temperature: int | None


class JobMeta(NamedTuple):
    absent: bool
    weather: bool
    presence: bool
    skip_replay: bool


app = cast(OrcFlask, _current_app)

bp = Blueprint("controls", __name__)

_DEVICE_TYPE_ORDER = {"Light": 0, "Chromecast": 2, "AC": 3}
_CACHE_CONTROL = {
    "controls.index": "max-age=3600",
    "controls.schedule": "max-age=3600",
    "controls.log": "no-store",
    "controls.presence": "no-store",
}
_CODESPAN_RE = re.compile(r"`([^`]+)`")


@bp.app_context_processor
def _version() -> dict[str, str]:
    return {"version": app.orc.version_manager.version}


@bp.app_template_filter("codespan")
def codespan(text: str) -> Markup:
    """Log messages mark config-provided names with backticks; render them as <code>."""
    return Markup(_CODESPAN_RE.sub(r"<code>\1</code>", str(escape(text))))  # nosemgrep: explicit-unescape-with-markup


@bp.errorhandler(404)
def not_found(exc: NotFound) -> tuple[dict[str, str], int]:
    return {"error": exc.description}, 404


@bp.after_request
def cache_control(response: Response) -> Response:
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    elif request.url_rule and (policy := _CACHE_CONTROL.get(request.url_rule.endpoint)):
        response.headers["Cache-Control"] = policy
    return response


@cache
def _hooks_bundle(scripts: tuple[tuple[str, Path], ...]) -> str:
    static = Path(__file__).parent / "static"
    gate = static / "hooks" / "are-you-sure.js"
    core = [static / "hooks.js", gate, *(p for p in (static / "hooks").glob("*.js") if p != gate)]
    files = [(p.name, p) for p in core] + list(scripts)
    return "\n".join(f"// --- {name}\n(() => {{\n{path.read_text()}}})();" for name, path in files)


@bp.route("/hooks.js")
def hooks() -> Response:
    return Response(_hooks_bundle(tuple(config.registry.scripts.items())), mimetype="text/javascript")


@cache
def _config_text(config_dir: str) -> str:
    return (Path(config_dir) / "config.orc").read_text()


class VersionManager:
    version = str(random.random())

    @classmethod
    def bump_version(cls) -> None:
        cls.version = str(random.random())

    @staticmethod
    def versioned(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not request.args.get("ignore-version") and not request.headers.get("orc-version") == VersionManager.version:
                api.log(
                    m.LogSource.SYSTEM,
                    Log.VERSION_MISMATCH.format(client=request.headers.get("orc-version"), server=VersionManager.version),
                    m.Manual(request.url_rule.endpoint if request.url_rule else request.path),
                )
                return {"version": VersionManager.version}, 412
            result = func(*args, **kwargs)
            if result is not None:
                return result
            VersionManager.version = str(random.random())
            return {"version": VersionManager.version}, 200

        return wrapper


@bp.route("/system/")
def cfg() -> str:
    today = api.local_now().date()
    tomorrow = today + timedelta(days=1)
    plugin_htmls = {name.rsplit("/", 1)[-1]: Markup("<pre>{}</pre>").format(text) for name, text in sorted(config.plugin_configs.items())}
    html = Markup("<pre>{}</pre>").format(_config_text(config.config_dir))

    states = [(title, fn()) for title, fn in config.registry.state_providers.items()]
    # One button per device: each actionable state row whose action is a
    # device-section plugin (row.action -> plugin name, row.name -> device).
    device_plugins = {p.name: p for p in config.plugins_in("device")}
    device_buttons = [row for title, rows in states for row in rows if row.action in device_plugins]

    return render_template(
        "system.html",
        html=html,
        plugin_htmls=plugin_htmls,
        plugins=config.plugins_in("system"),
        ad_hoc_routines=where(config.ad_hoc_routines, section="system"),
        ctx=app.orc,
        today_theme=api.calculate_theme(today),
        tomorrow_theme=api.calculate_theme(tomorrow),
        theme_override=api.current_theme_override(),
        lights=api.capture_lights(),
        sounds=api.capture_sounds(),
        sensors=api.capture_sensors(),
        retry_stats={s.id: s for s in api.fetch_retry_stats()},
        durations=api.fetch_durations(mapper=dict),
        plugin_states=states,
        device_buttons=device_buttons,
        device_plugins=device_plugins,
        registry=config.registry,
        scheduled_jobs=[(store, app.orc.scheduler.get_jobs(jobstore=store)) for store in (api.JOBSTORE_DEFAULT, api.JOBSTORE_MEMORY)],
    )


@bp.route("/device/")
def device() -> str:
    light_states = api.capture_lights(mapper=_states_by_name)
    sound_states = api.capture_sounds(mapper=_volumes_by_name)
    ac_temperatures = {s.what.name: s.temperature for s in api.capture_acs()}
    all_devices = list(chain.from_iterable(cls for name, cls in config.devices.items() if name in config.registry.controllable_devices))

    def make_device(d: Any) -> DeviceRow:
        level = _to_level(light_states.get(d.name))
        capabilities = {c.name for c in d.capabilities}
        return DeviceRow(
            name=d.label,
            id=d.name,
            type=d.kind,
            icon=config.registry.device_icons.get(d.kind, "light-bulb"),
            capabilities=capabilities,
            toggle=d.kind not in ("AC", "Chromecast", "USB") and "change_level" not in capabilities,
            level=level,
            on=level > 0,
            volume=sound_states.get(d.name, 0),
            temperature=ac_temperatures.get(d.name),
        )

    def sort_key(d: Any) -> tuple[int, bool, str]:
        has_level = "change_level" in {c.name for c in d.capabilities}
        return (_DEVICE_TYPE_ORDER.get(d.kind, 99), has_level, d.name)

    rooms = sorted({d.room for d in all_devices}, key=lambda r: r or "")
    devices_grouped = {room: [make_device(d) for d in sorted((d for d in all_devices if d.room == room), key=sort_key)] for room in rooms}
    return render_template("device.html", ctx=app.orc, devices_grouped=devices_grouped)


@bp.route("/api/run/<id>")
def run_routine(id: str) -> tuple[dict[str, Any], int]:
    if not api.run_action(app.orc, id, m.Manual(id), device=request.args.get("device"), skip_delay=request.args.get("skip_delay") == "1"):
        abort(404, "Unknown routine")
    return {"version": VersionManager.version}, 200


@bp.route("/api/presence/<name>/checkin")
@VersionManager.versioned
def checkin_presence(name: str) -> None:
    trigger = m.Manual(name)
    api.log(m.LogSource.MANUAL, Log.PRESENCE_CHECKED_IN.format(name=name), trigger)
    api.mark_present([name], api.local_now() + timedelta(hours=config.settings.checkin_hours), trigger)


@bp.route("/api/presence/<name>/expire")
@VersionManager.versioned
def expire_presence(name: str) -> None:
    trigger = m.Manual(name)
    api.log(m.LogSource.MANUAL, Log.PRESENCE_EXPIRED.format(name=name), trigger)
    api.expire_presence([name], trigger, force=True)


@bp.route("/")
def index() -> str:
    present_names = api.present_names()
    next_schedule = api.next_iot_job(present_names)

    return render_template(
        "scene.html",
        highlight_configs=[HighlightRow(n, s.strftime("%H:%M"), e.strftime("%H:%M")) for n, s, e in config.button_highlights],
        plugins=config.plugins_in("scene"),
        rooms=config.rooms,
        ad_hoc_routines=where(config.ad_hoc_routines, section="scene"),
        schedule_routines=config.schedule_routines,
        next_routine=next_schedule,
        durations=api.fetch_durations(mapper=dict),
    )


@bp.route("/log/")
def log() -> str:
    entries_grouped = [(day, list(day_entries)) for day, day_entries in groupby(api.log_entries(), key=lambda e: e.timestamp.date())]
    return render_template("log.html", entries_grouped=entries_grouped)


@bp.route("/api/schedule/<id>/pause")
@VersionManager.versioned
def pause(id: str) -> None:
    if not api.toggle_job(id):
        abort(404, "Unknown job")


@bp.route("/presence/")
def presence() -> str:
    last_seen = api.last_seen()
    present = api.present_names()
    rows = [
        PresenceStatus(
            name=name,
            hostnames=sorted(host for host, _ in entries),
            last_seen=last_seen.get(name),
            present=name in present,
        )
        for name, entries in config.people.items()
    ]
    return render_template("presence.html", rows=rows, strip_suffix="." + config.settings.lan_domain)


@bp.route("/api/device/<id>")
@VersionManager.versioned
def device_api(id: str) -> None:
    state = request.args.get("state")
    api.device_command(id, state, api.log(m.LogSource.MANUAL, Log.DEVICE_SET.format(id=id, state=state), m.Manual(id)))


@bp.route("/api/room/<id>")
def room(id: str) -> tuple[dict[str, Any], int]:
    if id not in config.rooms:
        abort(404, "Unknown room")
    api.run_room(id, request.args.get("state"), m.Manual(id))
    return {"version": VersionManager.version}, 200


@bp.route("/api/presence/state")
def presence_state() -> dict[str, Any]:
    return {"present": bool(api.present_names())}


@bp.route("/api/presence/run")
@VersionManager.versioned
def run_presence_check() -> None:
    api.rerun_presence_check(m.Manual.PRESENCE)


@bp.route("/schedule/")
def schedule() -> str:
    jobs = api.fetch_jobs_by_type(m.IotJob, mapper=_by_run_date)
    theme_override = api.current_theme_override()

    theme = (
        theme_override._replace(start=theme_override.start.isoformat(), end=theme_override.end.isoformat())  # type: ignore[arg-type]
        if theme_override
        else None
    )

    present_names = api.present_names()

    def meta(job: Job) -> JobMeta:
        rule = job.args[0].rule
        return JobMeta(
            absent=api.is_absent(rule, present_names),
            weather=api.weather_active(rule, job.trigger.run_date),
            presence=api.has_presence(rule),
            skip_replay=m.SKIP_REPLAY_TAG in rule.tags,
        )

    job_meta = {j.id: meta(j) for j in jobs}
    jobs_grouped = [(day, list(js)) for day, js in groupby(jobs, key=lambda j: j.trigger.run_date.date())]

    return render_template(
        "schedule.html",
        jobs_grouped=jobs_grouped,
        theme=theme,
        themes=sorted(config.themes),
        durations=api.fetch_durations(mapper=dict),
        job_meta=job_meta,
    )


@bp.route("/api/schedule/set_theme", methods=["POST"])
@VersionManager.versioned
def set_theme() -> None:
    name = request.form["theme"]
    if name and name not in config.themes:
        raise Exception(f"Unknown theme: {name}")
    start = date.fromisoformat(request.form["start"]) if name else None
    end = date.fromisoformat(request.form["end"]) if name else None
    api.apply_theme_change(app.orc, name, start, end, m.Manual(name) if name else m.Manual.THEME)


@bp.route("/api/announce", methods=["POST"])
@VersionManager.versioned
def announce() -> None:
    text = request.form["text"]
    entry = api.log(m.LogSource.MANUAL, Log.ANNOUNCE.format(text=text), m.Manual.ANNOUNCE)
    api.alert(m.Alarm.WARNING, text=text, entry=entry)


@bp.route("/api/alert.mp4")
def alert_mp4() -> Response:
    return Response(alerts.render_alert_video(request.args.get("text", "").replace("`", "")), mimetype="video/mp4")


@bp.route("/api/version")
def version() -> tuple[dict[str, Any], int]:
    return {"version": app.orc.version_manager.version}, 200


@bp.route("/api/durations")
def durations() -> tuple[dict[str, Any], int]:
    delays = api.action_delays()
    return {
        name: {"avg": round(avg, 3), "samples": samples, "delay": str(delays.get(name, timedelta()))}
        for name, (samples, avg) in api.duration_stats().items()
    }, 200


def _states_by_name(commands: m.Commands) -> dict[str, Any]:
    return {c.channel.one().name: c.value for c in commands}


def _volumes_by_name(sounds: tuple[m.SoundState, ...]) -> dict[str, int]:
    return {s.what.name: s.volume for s in sounds}


def _by_run_date(jobs: list[Job]) -> list[Job]:
    return sorted(jobs, key=lambda job: job.trigger.run_date)


def _to_level(state: object) -> int:
    if isinstance(state, int):
        return state
    return 100 if state == m.ON else 0
