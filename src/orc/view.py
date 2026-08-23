import random
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import date, timedelta
from functools import wraps
from itertools import chain, groupby
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from flask import Blueprint, Flask
from flask import current_app as _current_app
from flask import render_template, request
from flask.wrappers import Response
from markupsafe import Markup, escape

import orc
from orc import api, config
from orc import model as m
from orc.collections import where
from orc.locale import Log


class OrcFlask(Flask):
    orc: m.AppContext


app = cast(OrcFlask, _current_app)

bp = Blueprint("controls", __name__)

_DEVICE_TYPE_ORDER = {"Light": 0, "Chromecast": 2, "AC": 3}
_CODESPAN_RE = re.compile(r"`([^`]+)`")


@bp.app_template_filter("codespan")
def codespan(text: str) -> Markup:
    """Log messages mark config-provided names with backticks; render them as <code>."""
    return Markup(_CODESPAN_RE.sub(r"<code>\1</code>", str(escape(text))))  # nosemgrep: explicit-unescape-with-markup


@bp.after_request
def no_cache(response: Response) -> Response:
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/hooks.js")
def hooks() -> Response:
    static = Path(__file__).parent / "static"
    gate = static / "hooks" / "are-you-sure.js"
    core = [static / "hooks.js", gate, *(p for p in (static / "hooks").glob("*.js") if p != gate)]
    files = [(p.name, p) for p in core] + list(config.registry.scripts.items())
    return Response("\n".join(f"// --- {name}\n(() => {{\n{path.read_text()}}})();" for name, path in files), mimetype="text/javascript")


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
    plugins_dir = Path(config.config_dir) / "plugins"
    plugin_htmls: dict[str, str] = {}
    if plugins_dir.is_dir():
        for p in sorted(plugins_dir.glob("**/*.orc")):
            plugin_htmls[p.stem] = Markup("<pre>{}</pre>").format(p.read_text())
    html = Markup("<pre>{}</pre>").format((Path(config.config_dir) / "config.orc").read_text())

    states = [(title, fn()) for title, fn in config.registry.state_providers.items()]
    # One button per device: each actionable state row whose action is a
    # device-section plugin (row "action" -> plugin name, "name" -> device).
    device_plugins = {p.name: p for p in config.plugins_in("device")}
    device_buttons = [row for title, rows in states for row in rows if row.get("action") in device_plugins]

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
        retry_stats={s.id: s for s in api.fetch_retry_stats()},
        durations=dict(api.fetch_durations()),
        plugin_states=states,
        device_buttons=device_buttons,
        device_plugins=device_plugins,
        registry=config.registry,
        version=app.orc.version_manager.version,
    )


@bp.route("/device/")
def device() -> str:
    # captured lights are always enum members, not the class/set arm
    light_states = {c.what.name: c.state for c in api.capture_lights().items}  # type: ignore[union-attr]
    sound_states = {c.what.name: c.volume for c in api.capture_sounds().items}
    all_devices = list(chain.from_iterable(dt.cls for dt in config.registry.devices.values() if dt.controllable))

    def make_device(d: Any) -> SimpleNamespace:
        level = _to_level(light_states.get(d.name))
        capabilities = {c.name for c in d.capabilities}
        return SimpleNamespace(
            name=d.name.replace("_", " ").title(),
            id=d.name,
            type=type(d).__name__,
            icon=config.registry.devices[type(d).__name__].icon,
            capabilities=capabilities,
            toggle=type(d).__name__ not in ("AC", "Chromecast") and "change_level" not in capabilities,
            level=level,
            on=level > 0,
            volume=sound_states.get(d.name, 0),
        )

    def sort_key(d: Any) -> tuple[int, bool, str]:
        has_level = "change_level" in {c.name for c in d.capabilities}
        return (_DEVICE_TYPE_ORDER.get(type(d).__name__, 99), has_level, d.name)

    rooms = sorted({d.room for d in all_devices}, key=lambda r: r or "")
    devices_grouped = {room: [make_device(d) for d in sorted((d for d in all_devices if d.room == room), key=sort_key)] for room in rooms}
    return render_template("device.html", ctx=app.orc, devices_grouped=devices_grouped, version=app.orc.version_manager.version)


@bp.route("/api/run/<id>")
def run_routine(id: str) -> tuple[dict[str, Any], int]:
    if not api.run_action(app.orc, id, device=request.args.get("device"), skip_delay=request.args.get("skip_delay") == "1"):
        return {"error": "Unknown routine"}, 404
    return {"version": VersionManager.version}, 200


@bp.route("/api/presence/<name>/checkin")
@VersionManager.versioned
def checkin_presence(name: str) -> None:
    api.mark_present([name], when=api.local_now() + timedelta(hours=1))
    api.log(m.LogSource.MANUAL, Log.PRESENCE_CHECKED_IN.format(name=name))


@bp.route("/api/presence/<name>/expire")
@VersionManager.versioned
def expire_presence(name: str) -> None:
    api.expire_presence([name], force=True)
    api.log(m.LogSource.MANUAL, Log.PRESENCE_EXPIRED.format(name=name))


@bp.route("/")
def index() -> tuple[str, int, dict[str, str]]:
    present_names = api.present_names()
    next_schedule = api.next_iot_job(present_names)

    return (
        render_template(
            "scene.html",
            highlight_configs=[(n, s.strftime("%H:%M"), e.strftime("%H:%M")) for n, s, e in config.button_highlights],
            plugins=config.plugins_in("scene"),
            rooms=config.rooms,
            ad_hoc_routines=where(config.ad_hoc_routines, section="scene"),
            schedule_routines=config.schedule_routines,
            next_routine=next_schedule,
            durations=dict(api.fetch_durations()),
            version=app.orc.version_manager.version,
        ),
        200,
        {"Cache-control": "max-age=3600"},
    )


@bp.route("/log/")
def log() -> tuple[str, int, dict[str, str]]:
    entries_grouped = [(day, list(day_entries)) for day, day_entries in groupby(api.log_entries(), key=lambda e: e.timestamp.date())]
    return (
        render_template("log.html", version=app.orc.version_manager.version, entries_grouped=entries_grouped),
        200,
        {"Cache-control": "no-store"},
    )


@bp.route("/api/schedule/<id>/pause")
@VersionManager.versioned
def pause(id: str) -> tuple[dict[str, Any], int] | None:
    if not api.toggle_job(id):
        return {"error": "Unknown job"}, 404
    return None


@bp.route("/presence/")
def presence() -> tuple[str, int, dict[str, str]]:
    last_seen = api.last_seen()
    present = api.present_names()
    rows = [
        {
            "name": name,
            "hostnames": sorted(host for host, _ in entries),
            "last_seen": last_seen.get(name),
            "present": name in present,
        }
        for name, entries in config.people.items()
    ]
    return (
        render_template(
            "presence.html",
            version=app.orc.version_manager.version,
            rows=rows,
            strip_suffix="." + config.settings.lan_domain,
        ),
        200,
        {"Cache-control": "no-store"},
    )


@bp.route("/api/device/<id>")
@VersionManager.versioned
def device_api(id: str) -> None:
    api.device_command(id, request.args.get("state"))
    api.log(m.LogSource.MANUAL, Log.DEVICE_SET.format(id=id, state=request.args.get("state")))


@bp.route("/api/device/ac/<id>")
def ac(id: str) -> tuple[dict[str, Any], int]:
    state = request.args.get("state")
    try:
        bl_device = orc.AC[id]
    except KeyError:
        return {"error": "Unknown device"}, 404
    api.ac_command(
        bl_device,
        state,
        mode=request.args.get("mode"),
        fan=request.args.get("fan"),
        temp=int(t) if (t := request.args.get("temp")) else None,
    )
    return {"version": VersionManager.version}, 200


@bp.route("/api/room/<id>")
def room(id: str) -> tuple[dict[str, Any], int]:
    state = request.args.get("state")
    if id not in config.rooms:
        return {"error": "Unknown room"}, 404
    entry = api.log(m.LogSource.MANUAL, Log.ROOM_SET.format(id=id, state=state))
    with api.record_duration(id):
        if state == m.ON:
            api.dispatch(config.rooms[id], force=True, entry=entry)
        elif state == m.OFF:
            api.dispatch(m.Configs(*(replace(e, state=m.OFF) for e in config.rooms[id].items)), force=True, entry=entry)
        elif state == m.FOLLOW:
            api.dispatch(m.squish_configs(config.rooms_off, config.rooms[id]), force=True, entry=entry)
        else:
            raise Exception("Unknown state")
    return {"version": VersionManager.version}, 200


@bp.route("/api/presence/state")
def presence_state() -> dict[str, Any]:
    return {"present": bool(api.present_names())}


@bp.route("/api/presence/run")
@VersionManager.versioned
def run_presence_check() -> None:
    api.rerun_presence_check(app.orc)


@bp.route("/schedule/")
def schedule() -> tuple[str, int, dict[str, str]]:
    jobs = sorted(api.fetch_jobs_by_type(m.IotJob), key=lambda e: e.trigger.run_date)
    theme_override = api.current_theme_override()

    theme = (
        # dates rendered to ISO strings for the template
        theme_override._replace(start=theme_override.start.isoformat(), end=theme_override.end.isoformat())  # type: ignore[arg-type]
        if theme_override
        else None
    )

    present_names = api.present_names()
    absent_by_job = {j.id: api.is_absent(j.args[0].rule, present_names) for j in jobs}
    weather_by_job = {j.id: bool(api.matched_weather(j.args[0].rule, j.trigger.run_date)) for j in jobs}
    presence_by_job = {j.id: bool(api.matched_presence(j.args[0].rule)) for j in jobs}
    skip_replay_by_job = {j.id: j.args[0].rule.skip_replay for j in jobs}
    jobs_grouped = [(day, list(js)) for day, js in groupby(jobs, key=lambda j: j.trigger.run_date.date())]

    return (
        render_template(
            "schedule.html",
            version=app.orc.version_manager.version,
            jobs_grouped=jobs_grouped,
            theme=theme,
            themes=sorted(config.themes),
            durations=dict(api.fetch_durations()),
            absent_by_job=absent_by_job,
            weather_by_job=weather_by_job,
            presence_by_job=presence_by_job,
            skip_replay_by_job=skip_replay_by_job,
        ),
        200,
        {"Cache-control": "max-age=3600"},
    )


@bp.route("/api/schedule/set_theme", methods=["POST"])
@VersionManager.versioned
def set_theme() -> None:
    name = request.form["theme"]
    if name and name not in config.themes:
        raise Exception(f"Unknown theme: {name}")
    start = date.fromisoformat(request.form["start"]) if name else None
    end = date.fromisoformat(request.form["end"]) if name else None
    api.apply_theme_change(app.orc, name, start, end)


@bp.route("/api/version")
def version() -> tuple[dict[str, Any], int]:
    return {"version": app.orc.version_manager.version}, 200


@bp.route("/api/durations")
def durations() -> tuple[dict[str, Any], int]:
    return {name: {"avg": round(avg, 3), "samples": samples} for name, (samples, avg) in api.duration_stats().items()}, 200


def _to_level(state: object) -> int:
    if isinstance(state, int):
        return state
    return 100 if state == m.ON else 0
