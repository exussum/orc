from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Sequence

import requests
from apscheduler.triggers.date import DateTrigger
from orc_plugins.entrance_sensor import dal

from orc.collections import LockedDict
from orc.plugins import PluginCtx, build_ctx, plugin_config, requires_ctx

if TYPE_CHECKING:
    from orc import model as m

SNAPSHOT_NAME = "entrance_sensor"
JOB_ID = "trigger-sensor"

_battery: LockedDict[str, tuple[dal.SensorState, datetime]] = LockedDict()  # name -> (last nightly reading, when fetched)


@plugin_config(
    "entrance_sensor",
    schema={
        "Settings": ("Key", "Value"),
        "Messages": ("Log", "Message"),
        "Rules": ("Trigger", "Device", "State"),
        "Timed": ("Name", "Start", "Stop", "Device", "State"),
    },
)
def trigger_sensor(ctx: PluginCtx, sensor: SimpleNamespace, device_id: str, event: int) -> None:
    if int(device_id) != sensor.entrance_id:
        return

    if event == sensor.active_event:
        if ctx.scheduler.get_job(JOB_ID, jobstore=ctx.api.JOBSTORE_MEMORY):
            ctx.scheduler.remove_job(JOB_ID, jobstore=ctx.api.JOBSTORE_MEMORY)
        restore = _restorable(ctx, sensor, ctx.snapshot_manager.get(SNAPSHOT_NAME))
        timed_name, timed_rows = _timed_rows(ctx, sensor)
        ctx.api.log(ctx.api.local_now(), ctx.model.LogSource.PLUGIN, f"Entrance triggered: {timed_name}")
        ctx.api.dispatch(ctx.model.squish_configs(restore, _to_configs(ctx, [*sensor.rules.enter, *timed_rows])), force=True)
    elif event == sensor.inactive_event:
        ctx.api.dispatch(_to_configs(ctx, sensor.rules.inside, trigger=ctx.model.Trigger.SYSTEM))
        ctx.scheduler.add_job(
            _run_trigger_sensor_off,
            DateTrigger(ctx.api.local_now() + timedelta(minutes=sensor.cleanup_delay_minutes), timezone=ctx.config.tz),
            name="Trigger Sensor",
            id=JOB_ID,
            replace_existing=True,
            jobstore=ctx.api.JOBSTORE_MEMORY,
            args=(sensor,),
        )


@requires_ctx
def _run_trigger_sensor_off(sensor: SimpleNamespace, *, ctx: m.AppContext) -> None:
    plugin_ctx = build_ctx(ctx)

    plugin_ctx.api.expire_presence(list(plugin_ctx.api.last_seen()))
    present = plugin_ctx.api.check_presence(silent=True)
    door_open = not present and _door_open(plugin_ctx, sensor)

    if present or door_open:
        plugin_ctx.api.dispatch(_to_configs(plugin_ctx, sensor.rules.present))
        msg = sensor.log_door_open if door_open else sensor.log_present
    elif any(s.content for s in plugin_ctx.api.capture_sounds().items):
        # Visitor left, pet still listening: restore the pre-visit state
        plugin_ctx.snapshot_manager.resume(SNAPSHOT_NAME, plugin_ctx.model.Configs())
        plugin_ctx.api.dispatch(_to_configs(plugin_ctx, sensor.rules.absent))
        msg = sensor.log_absent
    else:
        end = plugin_ctx.api.local_now() + timedelta(minutes=sensor.snapshot)
        plugin_ctx.snapshot_manager.replace_config(SNAPSHOT_NAME, _to_configs(plugin_ctx, sensor.rules.shutdown), end)
        plugin_ctx.api.dispatch(_to_configs(plugin_ctx, sensor.rules.absent))
        msg = sensor.log_shutdown
    plugin_ctx.api.log(plugin_ctx.api.local_now(), plugin_ctx.model.LogSource.PLUGIN, msg)


@requires_ctx
def _run_poll_battery(*, ctx: m.AppContext) -> None:
    _poll_battery(build_ctx(ctx))


@plugin_config("entrance_sensor", schema={"Settings": ("Key", "Value")})
def _poll_battery(ctx: PluginCtx, sensor: SimpleNamespace) -> None:
    for name, device_id in (("front door", sensor.entrance_id), ("balcony door", sensor.patio_door_id)):
        try:
            state = dal.fetch_state(name, device_id)
        except requests.RequestException:
            continue
        _battery[name] = (state, ctx.api.local_now())
        if state.battery is not None and state.battery.is_critical:
            ctx.api.log(ctx.api.local_now(), ctx.model.LogSource.PLUGIN, f"Low battery on {name} ({state.battery.value})")


def battery_state() -> list[dict[str, Any]]:
    """Per-sensor battery rows for core's generic state renderer (needs a "name" key)."""
    return [
        {"name": s.name, "battery": s.battery.value if s.battery is not None else None, "checked": checked}
        for s, checked in _battery.values()
    ]


def _door_open(ctx: PluginCtx, sensor: SimpleNamespace) -> bool:
    # An open entrance door means someone is around even if presence hasn't seen them.
    try:
        state = dal.fetch_state("patio door", sensor.patio_door_id)
    except requests.RequestException:
        return False  # hub unreachable: fall back to the presence-only decision
    return state.attributes.get("contact") == "open"


def _timed_rows(ctx: PluginCtx, sensor: SimpleNamespace) -> tuple[str, Sequence[Any]]:
    # First group whose window contains now wins; a group's window is its first row.
    t = ctx.api.local_now().time()

    def in_window(row: Any) -> bool:
        if row.start <= row.stop:
            return row.start <= t < row.stop
        return t >= row.start or t < row.stop  # window wraps midnight

    return next(
        ((name, rows) for (name, rows) in vars(sensor.timed).items() if rows and in_window(rows[0])),
        ("(non window found)", ()),
    )


def _restorable(ctx: PluginCtx, sensor: SimpleNamespace, snapshot: m.SnapShot | None) -> m.Configs:
    # The snapshot is captured after the inside rule ran, so its state for those
    # lights is plugin-caused, not household state - don't replay it.
    if snapshot is None:
        return ctx.model.Configs()
    inside = {d for r in sensor.rules.inside for d in ((r.device,) if isinstance(r.device, Enum) else r.device)}
    return ctx.model.Configs(*[c for c in snapshot.routine.items if c.what not in inside])


def _to_configs(ctx: PluginCtx, rows: Sequence[Any], trigger: m.Trigger | None = None) -> m.Configs:
    return ctx.model.Configs(*[ctx.model.Config(r.device, r.state, trigger=trigger) for r in rows])
