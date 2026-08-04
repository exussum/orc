from __future__ import annotations

from datetime import timedelta
from enum import Enum
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Sequence

from apscheduler.triggers.date import DateTrigger

from orc.plugins import requires_ctx

if TYPE_CHECKING:
    from orc import model as m

SNAPSHOT_NAME = "entrance_sensor"
JOB_ID = "trigger-sensor"
TRIGGER_MSG = "Entrance sensor triggered"


def _on_sensor_event(
    ctx: m.AppContext, sensor: SimpleNamespace, sensor_ids: set[int], device: m.DeviceState, attribute: str, old: Any, new: Any
) -> None:
    if device.id not in sensor_ids:
        return
    if attribute == "battery":
        level = ctx.model.BatteryLevel.from_fraction(new, 100)
        if level.is_critical:
            ctx.api.log(ctx.model.LogSource.PLUGIN, f"Low battery on `{device.name}` ({level.value})")
    elif _entrance_motion_changed(sensor, device, attribute, old, new):
        # The listener runs on the mqtt network thread, where a publish is only
        # queued until the callback returns: dispatching here holds the light
        # command behind the chromecast I/O the same dispatch triggers. Run on
        # the scheduler's worker; None grace so a busy worker delays, never drops.

        # Both motion events of one visit land under a single log entry: reuse
        # the latest entry if it's still the trigger message.
        entries = ctx.api.log_entries()
        if entries and entries[0].action == TRIGGER_MSG:
            log_entry = entries[0]
        else:
            log_entry = ctx.api.log(ctx.model.LogSource.PLUGIN, TRIGGER_MSG)

        ctx.scheduler.add_job(
            _run_motion,
            DateTrigger(ctx.api.local_now(), timezone=ctx.config.tz),
            name="Entrance Motion",
            misfire_grace_time=None,
            jobstore=ctx.api.JOBSTORE_MEMORY,
            args=(sensor, new, log_entry),
        )


def _entrance_motion_changed(sensor: SimpleNamespace, device: m.DeviceState, attribute: str, old: Any, new: Any) -> bool:
    return attribute == "motion" and old != new and device.id == sensor.entrance_id and new in (sensor.active_event, sensor.inactive_event)


@requires_ctx
def _run_motion(sensor: SimpleNamespace, new: Any, log_entry: m.LogEntry, *, ctx: m.AppContext) -> None:
    if new == sensor.active_event:
        if ctx.scheduler.get_job(JOB_ID, jobstore=ctx.api.JOBSTORE_MEMORY):
            ctx.scheduler.remove_job(JOB_ID, jobstore=ctx.api.JOBSTORE_MEMORY)
        restore = _restorable(ctx, sensor, ctx.snapshot_manager.get(SNAPSHOT_NAME))
        timed_name, timed_rows = _timed_rows(ctx, sensor)
        log_entry.add(ctx.model.LogSource.PLUGIN, f"Applying `{timed_name}` rules")
        ctx.api.dispatch(ctx.model.squish_configs(restore, _to_configs(ctx, [*sensor.rules.enter, *timed_rows])), force=True)
    elif new == sensor.inactive_event:
        ctx.api.dispatch(_to_configs(ctx, sensor.rules.inside, trigger=ctx.model.Trigger.SYSTEM))
        ctx.scheduler.add_job(
            _run_trigger_sensor_off,
            DateTrigger(ctx.api.local_now() + timedelta(minutes=sensor.cleanup_delay_minutes), timezone=ctx.config.tz),
            name="Trigger Sensor",
            id=JOB_ID,
            replace_existing=True,
            jobstore=ctx.api.JOBSTORE_MEMORY,
            args=(sensor, log_entry),
        )


@requires_ctx
def _run_trigger_sensor_off(sensor: SimpleNamespace, log_entry: m.LogEntry, *, ctx: m.AppContext) -> None:
    ctx.api.expire_presence(list(ctx.api.last_seen()))
    present = ctx.api.check_presence(silent=True)
    door_open = not present and _door_open(ctx, sensor)

    if present or door_open:
        ctx.api.dispatch(_to_configs(ctx, sensor.rules.present))
        msg = sensor.log_door_open if door_open else sensor.log_present
    elif any(s.content for s in ctx.api.capture_sounds().items):
        # Visitor left, pet still listening: restore the pre-visit state
        ctx.snapshot_manager.resume(SNAPSHOT_NAME, ctx.model.Configs())
        ctx.api.dispatch(_to_configs(ctx, sensor.rules.absent))
        msg = sensor.log_absent
    else:
        end = ctx.api.local_now() + timedelta(minutes=sensor.snapshot)
        ctx.snapshot_manager.replace_config(SNAPSHOT_NAME, _to_configs(ctx, sensor.rules.shutdown), end, label=SNAPSHOT_NAME)
        ctx.api.dispatch(_to_configs(ctx, sensor.rules.absent))
        msg = sensor.log_shutdown
    log_entry.add(ctx.model.LogSource.PLUGIN, msg)


def battery_state(ctx: m.AppContext, sensor_ids: set[int]) -> list[dict[str, Any]]:
    devices = ctx.api.device_states()
    return [
        {
            "name": d.name,
            "battery": ctx.model.BatteryLevel.from_fraction(battery, 100).value if battery is not None else None,
            "last_activity": d.last_activity,
        }
        for device_id in sorted(sensor_ids)
        if (d := _sensor(devices, device_id)) is not None
        for battery in (d.attributes.get("battery"),)
    ]


def _door_open(ctx: m.AppContext, sensor: SimpleNamespace) -> bool:
    # An open entrance door means someone is around even if presence hasn't seen them.
    # A door never seen over MQTT reads as closed, falling back to the presence-only
    # decision like the old unreachable-hub path.
    device = _sensor(ctx.api.device_states(), sensor.patio_door_id)
    return device is not None and device.attributes.get("contact") == "open"


def _sensor(devices: Sequence[m.DeviceState], device_id: int) -> m.DeviceState | None:
    return next((d for d in devices if d.id == device_id), None)


def _timed_rows(ctx: m.AppContext, sensor: SimpleNamespace) -> tuple[str, Sequence[Any]]:
    # First group whose window contains now wins; a group's window is its first row.
    t = ctx.api.local_now().time()

    def in_window(row: Any) -> bool:
        if row.start <= row.stop:
            return row.start <= t < row.stop
        return t >= row.start or t < row.stop  # window wraps midnight

    return next(
        ((name, rows) for (name, rows) in vars(sensor.timed).items() if rows and in_window(rows[0])),
        ("(no window found)", ()),
    )


def _restorable(ctx: m.AppContext, sensor: SimpleNamespace, snapshot: m.SnapShot | None) -> m.Configs:
    # The snapshot is captured after the inside rule ran, so its state for those
    # lights is plugin-caused, not household state - don't replay it.
    if snapshot is None:
        return ctx.model.Configs()
    inside = {d for r in sensor.rules.inside for d in ((r.device,) if isinstance(r.device, Enum) else r.device)}
    return ctx.model.Configs(*[c for c in snapshot.routine.items if c.what not in inside])


def _to_configs(ctx: m.AppContext, rows: Sequence[Any], trigger: m.Trigger | None = None) -> m.Configs:
    return ctx.model.Configs(*[ctx.model.Config(r.device, r.state, trigger=trigger) for r in rows])
