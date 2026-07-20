import random
from collections.abc import Callable
from dataclasses import replace
from datetime import date, timedelta
from functools import wraps
from itertools import chain, groupby
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from apscheduler.triggers.date import DateTrigger
from flask import Blueprint, Flask
from flask import current_app as _current_app
from flask import render_template, request
from flask.wrappers import Response
from mistletoe import Document, HtmlRenderer

import orc
from orc import api, config
from orc import model as m
from orc import plugins
from orc.collections import where
from orc.locale import Log
from orc.security import safe_html


class OrcFlask(Flask):
    orc: m.AppContext


app = cast(OrcFlask, _current_app)

bp = Blueprint("controls", __name__)

_DEVICE_TYPE_ORDER = {"Light": 0, "Chromecast": 2, "AC": 3}


@bp.after_request
def no_cache(response: Response) -> Response:
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


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
                    api.local_now(),
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
    today = date.today()
    tomorrow = today + timedelta(days=1)
    plugins_dir = Path(config.config_dir) / "plugins"
    plugin_htmls: dict[str, str] = {}
    if plugins_dir.is_dir():
        for p in sorted(plugins_dir.glob("**/*.md")):
            with open(p) as f:
                plugin_htmls[p.stem] = safe_html(HtmlRenderer().render(Document(f)))
    with open(Path(config.config_dir) / "config.md") as f:
        html = safe_html(HtmlRenderer().render(Document(f)))

    states = [(title, fn()) for title, fn in config.registry.state_providers.items()]
    # One button per device: each actionable state row whose action is a
    # device-section plugin (row "action" -> plugin id, "name" -> device).
    device_buttons = [row for title, rows in states for row in rows if row.get("action") in where(config.plugins, section="device")]

    return render_template(
        "system.html",
        html=html,
        plugin_htmls=plugin_htmls,
        plugins=where(config.plugins, section="system"),
        ad_hoc_routines=where(config.ad_hoc_routines, section="system"),
        ctx=app.orc,
        today_theme=api.calculate_theme(today),
        tomorrow_theme=api.calculate_theme(tomorrow),
        theme_override=api.current_theme_override(),
        lights=api.capture_lights(),
        sounds=api.capture_sounds(),
        durations=dict(api.fetch_durations()),
        plugin_states=states,
        device_buttons=device_buttons,
        device_plugins=where(config.plugins, section="device"),
        registry=config.registry,
        version=app.orc.version_manager.version,
    )


@bp.route("/device/")
def device() -> str:
    light_states = {c.what.name: c.state for c in api.capture_lights().items}  # type: ignore[union-attr]  # captured lights are always enum members, not the class/set arm
    sound_states = {c.what.name: c.volume for c in api.capture_sounds().items}
    all_devices = list(chain.from_iterable(dt.cls for dt in config.registry.devices.values() if dt.controllable))

    def make_device(d: Any) -> SimpleNamespace:
        level = _to_level(light_states.get(d.name))
        return SimpleNamespace(
            name=d.name.replace("_", " ").title(),
            id=d.name,
            type=type(d).__name__,
            icon=config.registry.devices[type(d).__name__].icon,
            capabilities={c.name for c in d.capabilities},
            level=level,
            on=level > 0,
            volume=sound_states.get(d.name, 0),
        )

    def sort_key(d: Any) -> tuple[int, bool, str]:
        has_level = "change_level" in {c.name for c in d.capabilities}
        return (_DEVICE_TYPE_ORDER.get(type(d).__name__, 99), has_level, d.name)

    rooms = sorted({d.room for d in all_devices}, key=lambda r: r or "")
    devices_grouped = {room: [make_device(d) for d in sorted((d for d in all_devices if d.room == room), key=sort_key)] for room in rooms}
    return render_template("device.html", ctx=app.orc, devices_grouped=devices_grouped)


@bp.route("/api/rebuild_jobs")
def rebuild_jobs() -> tuple[dict[str, Any], int]:
    with api.record_duration("Rebuild Jobs"):
        app.orc.scheduler.remove_all_jobs()
        api.setup_scheduler(app.orc)
    return {"version": VersionManager.version}, 200


@bp.route("/api/run/<id>")
def run_routine(id: str) -> tuple[dict[str, Any], int]:
    resolved = _resolve_run_action(id)
    if resolved is None:
        return {"error": "Unknown routine"}, 404
    action, delay = resolved

    @api.requires_ctx
    def run(ctx: m.AppContext) -> None:
        api.log(api.local_now(), m.LogSource.MANUAL, id)
        action()

    with api.record_duration(id):
        if delay:
            when = api.local_now() + delay
            api.log(api.local_now(), m.LogSource.MANUAL, Log.TASK_QUEUED.format(id=id, when=when))
            job_id = f"run-{id}-{when.isoformat()}"
            app.orc.scheduler.add_job(run, DateTrigger(when, timezone=config.tz), id=job_id, jobstore=api.JOBSTORE_MEMORY)
        else:
            run(ctx=app.orc)
    return {"version": VersionManager.version}, 200


@bp.route("/api/presence/<name>/checkin")
@VersionManager.versioned
def checkin_presence(name: str) -> None:
    api.mark_present([name], when=api.local_now() + timedelta(hours=1))
    api.log(api.local_now(), m.LogSource.MANUAL, Log.PRESENCE_CHECKED_IN.format(name=name))


@bp.route("/api/presence/<name>/expire")
@VersionManager.versioned
def expire_presence(name: str) -> None:
    api.expire_presence([name], force=True)
    api.log(api.local_now(), m.LogSource.MANUAL, Log.PRESENCE_EXPIRED.format(name=name))


@bp.route("/api/hubitat/callback", methods=["POST"])
def hubitat_callback() -> tuple[dict[str, Any], int]:
    ctx = plugins.build_ctx(app.orc)
    device_id = request.json["content"]["deviceId"]
    value = request.json["content"]["value"]
    for plugin in where(config.plugins, section="hubitat").values():
        plugin.func(ctx, device_id, value)
    return {}, 200


@bp.route("/")
def index() -> tuple[str, int, dict[str, str]]:
    present_names = api.present_names()
    next_schedule = api.next_iot_job(app.orc.scheduler, present_names)

    return (
        render_template(
            "scene.html",
            highlight_configs=[(n, s.strftime("%H:%M"), e.strftime("%H:%M")) for n, s, e in config.button_highlight_configs],
            plugins=where(config.plugins, section="scene"),
            room_configs=config.room_configs,
            ad_hoc_routines=where(config.ad_hoc_routines, section="scene"),
            schedule_routines=config.schedule_routines,
            next_routine=next_schedule,
            durations=dict(api.fetch_durations()),
            version=app.orc.version_manager.version,
        ),
        200,
        {"Cache-control": "max-age=604800"},
    )


@bp.route("/log/")
def log() -> tuple[str, int, dict[str, str]]:
    entries_grouped = [
        (day, [list(run) for _, run in groupby(day_entries, key=lambda e: (e.source, e.action))])
        for day, day_entries in groupby(api.log_entries(), key=lambda e: e.timestamp.date())
    ]
    return (
        render_template("log.html", version=app.orc.version_manager.version, entries_grouped=entries_grouped),
        200,
        {"Cache-control": "no-store"},
    )


@bp.route("/api/schedule/<id>/pause")
@VersionManager.versioned
def pause(id: str) -> tuple[dict[str, Any], int] | None:
    job = app.orc.scheduler.get_job(id)
    if job is None:
        return {"error": "Unknown job"}, 404
    if job.next_run_time:
        job.pause()
    else:
        job.resume()
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
            strip_suffix="." + config.root_domain,
        ),
        200,
        {"Cache-control": "no-store"},
    )


@bp.route("/api/device/<id>")
def device_api(id: str) -> tuple[dict[str, Any], int]:
    api.device_command(id, request.args.get("state"))
    return {"version": VersionManager.version}, 200


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
    if id not in config.room_configs:
        return {"error": "Unknown room"}, 404
    with api.record_duration(id):
        if state == m.ON:
            api.dispatch(config.room_configs[id], force=True)
        elif state == m.OFF:
            api.dispatch(m.Configs(*(replace(e, state=m.OFF) for e in config.room_configs[id].items)), force=True)
        elif state == m.FOLLOW:
            api.dispatch(m.squish_configs(config.room_configs_off, config.room_configs[id]), force=True)
        else:
            raise Exception("Unknown state")
    api.log(api.local_now(), m.LogSource.MANUAL, Log.ROOM_SET.format(id=id, state=state))
    return {"version": VersionManager.version}, 200


@bp.route("/api/presence/run")
@VersionManager.versioned
def run_presence_check() -> tuple[dict[str, Any], int] | None:
    job = app.orc.scheduler.get_job("presence-cron")
    if job is None:
        return {"error": "Unknown job"}, 404
    api.log(api.local_now(), m.LogSource.MANUAL, Log.PRESENCE_RESCAN)
    api.delete_all_presence()
    job.func(ctx=app.orc)
    return None


@bp.route("/schedule/")
def schedule() -> tuple[str, int, dict[str, str]]:
    jobs = sorted(api.jobs_by_type(app.orc.scheduler, m.IotJob), key=lambda e: e.trigger.run_date)
    theme_override = api.current_theme_override()

    theme = (
        theme_override._replace(start=theme_override.start.isoformat(), end=theme_override.end.isoformat())  # type: ignore[arg-type]  # dates rendered to ISO strings for the template
        if theme_override
        else None
    )

    present_names = api.present_names()
    absent_by_job = {j.id: api.is_absent(j.args[0].rule, present_names) for j in jobs}
    weather_by_job = {j.id: bool(api.matched_weather(j.args[0].rule, j.trigger.run_date)) for j in jobs}
    presence_by_job = {j.id: bool(api.matched_presence(j.args[0].rule)) for j in jobs}
    jobs_grouped = [(day, list(js)) for day, js in groupby(jobs, key=lambda j: j.trigger.run_date.date())]

    return (
        render_template(
            "schedule.html",
            version=app.orc.version_manager.version,
            jobs_grouped=jobs_grouped,
            theme=theme,
            durations=dict(api.fetch_durations()),
            absent_by_job=absent_by_job,
            weather_by_job=weather_by_job,
            presence_by_job=presence_by_job,
        ),
        200,
        {"Cache-control": "max-age=604800"},
    )


@bp.route("/api/schedule/set_theme", methods=["POST"])
@VersionManager.versioned
def set_theme() -> None:
    name = request.form["theme"]
    start = date.fromisoformat(request.form["start"]) if name else None
    end = date.fromisoformat(request.form["end"]) if name else None
    api.apply_theme_change(app.orc, name, start, end)


@bp.route("/api/version")
def version() -> tuple[dict[str, Any], int]:
    return {"version": app.orc.version_manager.version}, 200


@bp.route("/api/durations")
def durations() -> tuple[dict[str, Any], int]:
    return dict(api.fetch_durations()), 200


def _to_level(state: object) -> int:
    if isinstance(state, int):
        return state
    return 100 if state == m.ON else 0


def _resolve_run_action(id: str) -> tuple[Callable[[], None], timedelta] | None:
    """Resolve a run id to (action, delay), or None if the id is unknown."""
    if id == api.ORC_SYSTEM_SNAPSHOT:
        return lambda: app.orc.snapshot_manager.resume(api.ORC_SYSTEM_SNAPSHOT, config.default_config), timedelta()
    elif id in config.plugins:
        params = {"device": device} if (device := request.args.get("device")) else {}
        return lambda: plugins.execute_plugin(app.orc, id, **params), config.plugins[id].delay
    elif id in config.schedule_routines:
        return lambda: api.dispatch(config.schedule_routines[id], force=True), timedelta()
    elif id in config.ad_hoc_routines:
        routine = config.ad_hoc_routines[id]
        if routine.snapshot and not app.orc.snapshot_manager.active(api.ORC_SYSTEM_SNAPSHOT):
            # Don't stack snapshots
            snap = routine.snapshot
            return lambda: app.orc.snapshot_manager.replace_config(api.ORC_SYSTEM_SNAPSHOT, routine, api.local_now() + snap), timedelta()
        base = (config.reset_config,) if routine.reset else ()
        return lambda: api.dispatch(m.squish_configs(*base, routine)), routine.delay
    return None
