from datetime import timedelta
from types import SimpleNamespace
from typing import Any, Sequence

from apscheduler.triggers.date import DateTrigger

from orc import model as m
from orc.plugins import requires_ctx

SNAPSHOT_NAME = "entrance_sensor"
JOB_ID = "trigger-sensor"
TRIGGER_MSG = "Entrance sensor triggered"
CLEARED_MSG = "Motion cleared, running `{routine_name}`, cleanup in {minutes} minutes"


class Log(m.LogSourceEnum):
    ENTRANCE = "entrance"


def _on_sensor_event(ctx: m.AppContext, sensor: SimpleNamespace, device: m.DeviceState, attribute: str, old: Any, new: Any) -> None:
    if device.id not in (sensor.setting.entrance.value, sensor.setting.patio_door.value):
        return
    if attribute == "battery":
        level = m.BatteryLevel.from_fraction(new, 100)
        if level.is_critical:
            ctx.api.log(
                Log.ENTRANCE, f"Low battery on `{device.name}` ({level.value})", trigger=m.Broker(id=str(device.id), source="hubitat")
            )
    elif _entrance_motion_changed(sensor, device, attribute, old, new):
        # The listener runs on the mqtt network thread, where a publish is only
        # queued until the callback returns: dispatching here holds the light
        # command behind the chromecast I/O the same dispatch triggers. Run on
        # the scheduler's worker; None grace so a busy worker delays, never drops.
        log_entry = ctx.api.log(Log.ENTRANCE, TRIGGER_MSG, trigger=m.Broker(id=str(device.id), source="hubitat"))
        ctx.scheduler.add_job(
            _run_motion,
            DateTrigger(ctx.api.local_now(), timezone=ctx.config.settings.tz),
            name="Entrance Motion",
            misfire_grace_time=None,
            jobstore=ctx.api.JOBSTORE_MEMORY,
            args=(sensor, new, log_entry),
        )


def _entrance_motion_changed(sensor: SimpleNamespace, device: m.DeviceState, attribute: str, old: Any, new: Any) -> bool:
    return (
        attribute == "motion"
        and old != new
        and device.id == sensor.setting.entrance.value
        and new in (sensor.setting.active_event, sensor.setting.inactive_event)
    )


@requires_ctx
def _run_motion(sensor: SimpleNamespace, new: Any, log_entry: m.LogEntry, *, ctx: m.AppContext) -> None:
    if new == sensor.setting.active_event:
        ctx.api.resume_presence(log_entry.trigger)
        if ctx.scheduler.get_job(JOB_ID, jobstore=ctx.api.JOBSTORE_MEMORY):
            ctx.scheduler.remove_job(JOB_ID, jobstore=ctx.api.JOBSTORE_MEMORY)
        restore = _restorable(ctx, sensor, ctx.engine.pop_snapshot(SNAPSHOT_NAME, ctx.api.local_now()))
        timed_name, timed_commands = _timed_commands(ctx, sensor)
        log_entry.add(Log.ENTRANCE, f"Applying `{timed_name}` rules")
        ctx.api.dispatch(m.squish((*restore, *timed_commands)), force=True, entry=log_entry)
    elif new == sensor.setting.inactive_event:
        log_entry.add(Log.ENTRANCE, CLEARED_MSG.format(routine_name=sensor.rules.inside, minutes=sensor.setting.cleanup_delay_minutes))
        ctx.api.run_action(ctx, sensor.rules.inside)
        ctx.api.pause_presence()
        ctx.scheduler.add_job(
            _run_trigger_sensor_off,
            DateTrigger(ctx.api.local_now() + timedelta(minutes=sensor.setting.cleanup_delay_minutes), timezone=ctx.config.settings.tz),
            name="Trigger Sensor",
            id=JOB_ID,
            replace_existing=True,
            jobstore=ctx.api.JOBSTORE_MEMORY,
            args=(sensor, log_entry),
        )


@requires_ctx
def _run_trigger_sensor_off(sensor: SimpleNamespace, log_entry: m.LogEntry, *, ctx: m.AppContext) -> None:
    present = ctx.api.check_presence(log_entry.trigger)
    ctx.api.resume_presence(log_entry.trigger)
    people = present - {sensor.setting.listener}
    door_open = not people and _door_open(ctx, sensor)

    if people or door_open:
        ctx.api.run_action(ctx, sensor.rules.present)
        msg = sensor.message.log_door_open if door_open else sensor.message.log_present
    elif sensor.setting.listener in present:
        # Visitor left, the listener stayed: restore the pre-visit state
        ctx.engine.restore_scene(ctx, SNAPSHOT_NAME, (), log_entry)
        ctx.api.run_action(ctx, sensor.rules.absent)
        msg = sensor.message.log_absent
    else:
        end = ctx.api.local_now() + timedelta(minutes=sensor.setting.snapshot)
        ctx.engine.override_scene(
            ctx, SNAPSHOT_NAME, ctx.config.ad_hoc_routines[sensor.rules.shutdown].commands, end, SNAPSHOT_NAME, log_entry
        )
        msg = sensor.message.log_shutdown
    log_entry.add(Log.ENTRANCE, msg)


def battery_state(ctx: m.AppContext, sensor: SimpleNamespace) -> list[m.DeviceStatus]:
    devices = ctx.api.device_states()
    statuses = [
        m.DeviceStatus(
            name=d.name if d else (member.label or member.name),
            details={
                "battery": m.BatteryLevel.from_fraction(battery, 100).value if battery is not None else None,
                "last_activity": d.last_activity if d else None,
            },
        )
        for member in (sensor.setting.entrance, sensor.setting.patio_door)
        for d in (_sensor(devices, member.value),)
        for battery in (d.attributes.get("battery") if d else None,)
    ]
    return sorted(statuses, key=lambda status: status.name)


def _door_open(ctx: m.AppContext, sensor: SimpleNamespace) -> bool:
    # An open entrance door means someone is around even if presence hasn't seen them.
    # A door never seen over MQTT reads as closed, falling back to the presence-only
    # decision like the old unreachable-hub path.
    device = _sensor(ctx.api.device_states(), sensor.setting.patio_door.value)
    return device is not None and device.attributes.get("contact") == "open"


def _sensor(devices: Sequence[m.DeviceState], device_id: int) -> m.DeviceState | None:
    return next((d for d in devices if d.id == device_id), None)


def _timed_commands(ctx: m.AppContext, sensor: SimpleNamespace) -> tuple[str, m.Commands]:
    # First group whose window contains now wins; a group's window is its first row.
    t = ctx.api.local_now().time()

    def in_window(row: Any) -> bool:
        if row.start <= row.stop:
            return row.start <= t < row.stop
        return t >= row.start or t < row.stop  # window wraps midnight

    return next(
        ((name, tuple(c for row in rows for c in row.commands)) for (name, rows) in sensor.timed.items() if rows and in_window(rows[0])),
        ("(no window found)", ()),
    )


def _restorable(ctx: m.AppContext, sensor: SimpleNamespace, snapshot: m.SnapShot | None) -> m.Commands:
    # The snapshot is captured after the inside rule ran, so its state for those
    # lights is plugin-caused, not household state - don't replay it.
    if snapshot is None:
        return ()
    inside = {d for c in ctx.config.ad_hoc_routines[sensor.rules.inside].commands for d in c.channel.all()}
    return tuple(c for c in snapshot.routine if c.channel.one() not in inside)
