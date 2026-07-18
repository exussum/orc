from __future__ import annotations

from datetime import timedelta
from enum import Enum
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Sequence

from apscheduler.triggers.date import DateTrigger

from orc.plugins import PluginCtx, build_ctx, plugin_config, requires_ctx

if TYPE_CHECKING:
    from orc import model as m

SNAPSHOT_NAME = "entrance_sensor"
JOB_ID = "trigger-sensor"


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
        snapshot = _restorable(ctx, sensor, ctx.snapshot_manager.get(SNAPSHOT_NAME))
        timed_name, timed_rows = _timed_rows(ctx, sensor)
        ctx.api.log(ctx.api.local_now(), ctx.model.LogSource.SYSTEM, f"Entrance triggered: {timed_name}")
        ctx.api.dispatch(ctx.model.squish_configs(snapshot, _to_configs(ctx, [*sensor.rules.enter, *timed_rows])), force=True)
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

    if present:
        plugin_ctx.api.dispatch(_to_configs(plugin_ctx, sensor.rules.present))
        msg = sensor.log_present
    elif any(s.content for s in plugin_ctx.api.capture_sounds().items):
        plugin_ctx.api.dispatch(_to_configs(plugin_ctx, sensor.rules.absent))
        msg = sensor.log_absent
    else:
        end = plugin_ctx.api.local_now() + timedelta(minutes=sensor.snapshot)
        plugin_ctx.snapshot_manager.replace_config(SNAPSHOT_NAME, _to_configs(plugin_ctx, sensor.rules.shutdown), end)
        plugin_ctx.api.dispatch(_to_configs(plugin_ctx, sensor.rules.absent))
        msg = sensor.log_shutdown
    plugin_ctx.api.log(plugin_ctx.api.local_now(), plugin_ctx.model.LogSource.SYSTEM, msg)


def _timed_rows(ctx: PluginCtx, sensor: SimpleNamespace) -> tuple[str, Sequence[Any]]:
    # First group whose window contains now wins; a group's window is its first row.
    t = ctx.api.local_now().time()

    def in_window(row: Any) -> bool:
        if row.start <= row.stop:
            return row.start <= t < row.stop
        return t >= row.start or t < row.stop  # window wraps midnight

    return next(((name, rows) for (name, rows) in vars(sensor.timed).items() if in_window(rows[0])), ("(non window found)", ()))


def _restorable(ctx: PluginCtx, sensor: SimpleNamespace, snapshot: m.SnapShot | None) -> m.Configs:
    # The snapshot is captured after the inside rule ran, so its state for those
    # lights is plugin-caused, not household state - don't replay it.
    if snapshot is None:
        return ctx.model.Configs()
    inside = {d for r in sensor.rules.inside for d in ((r.device,) if isinstance(r.device, Enum) else r.device)}
    return ctx.model.Configs(*[c for c in snapshot.routine.items if c.what not in inside])


def _to_configs(ctx: PluginCtx, rows: Sequence[Any], trigger: m.Trigger | None = None) -> m.Configs:
    return ctx.model.Configs(*[ctx.model.Config(r.device, r.state, trigger=trigger) for r in rows])
