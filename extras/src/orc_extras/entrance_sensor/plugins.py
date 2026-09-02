from datetime import timedelta
from types import SimpleNamespace
from typing import Any, Sequence

from apscheduler.triggers.date import DateTrigger

from orc import model as m
from orc.plugins import requires_ctx

SNAPSHOT_NAME = "entrance_sensor"
JOB_ID = "trigger-sensor"
MAX_ON_JOB_ID = "entrance-max-on"
TRIGGER_MSG = "Entrance sensor triggered"


class Log(m.LogSourceEnum):
    ENTRANCE = "entrance"


def _on_sensor_event(
    ctx: m.AppContext, sensor: SimpleNamespace, sensor_ids: set[int], device: m.DeviceState, attribute: str, old: Any, new: Any
) -> None:
    if device.id not in sensor_ids:
        return
    if attribute == "battery":
        level = m.BatteryLevel.from_fraction(new, 100)
        if level.is_critical:
            ctx.api.log(Log.ENTRANCE, f"Low battery on `{device.name}` ({level.value})")
    elif attribute == "switch" and device.id in _inside_light_ids(sensor) and old != new:
        job_id = f"{MAX_ON_JOB_ID}-{device.id}"
        if new == m.ON:
            ctx.scheduler.add_job(
                _run_max_on,
                DateTrigger(ctx.api.local_now() + timedelta(minutes=sensor.setting.entrance_max_on), timezone=ctx.config.settings.tz),
                name=f"Entrance Max On {device.name}",
                id=job_id,
                replace_existing=True,
                jobstore=ctx.api.JOBSTORE_MEMORY,
                args=(sensor, device.name),
            )
        elif ctx.scheduler.get_job(job_id, jobstore=ctx.api.JOBSTORE_MEMORY):
            ctx.scheduler.remove_job(job_id, jobstore=ctx.api.JOBSTORE_MEMORY)
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
            log_entry = ctx.api.log(Log.ENTRANCE, TRIGGER_MSG)

        ctx.scheduler.add_job(
            _run_motion,
            DateTrigger(ctx.api.local_now(), timezone=ctx.config.settings.tz),
            name="Entrance Motion",
            misfire_grace_time=None,
            jobstore=ctx.api.JOBSTORE_MEMORY,
            args=(sensor, new, log_entry),
        )


def _inside_light_ids(sensor: SimpleNamespace) -> set[int]:
    return {d.value for r in sensor.rules.inside for d in r.devices.all()}


def _entrance_motion_changed(sensor: SimpleNamespace, device: m.DeviceState, attribute: str, old: Any, new: Any) -> bool:
    return (
        attribute == "motion"
        and old != new
        and device.id == sensor.setting.entrance_id
        and new in (sensor.setting.active_event, sensor.setting.inactive_event)
    )


@requires_ctx
def _run_motion(sensor: SimpleNamespace, new: Any, log_entry: m.LogEntry, *, ctx: m.AppContext) -> None:
    if new == sensor.setting.active_event:
        if ctx.scheduler.get_job(JOB_ID, jobstore=ctx.api.JOBSTORE_MEMORY):
            ctx.scheduler.remove_job(JOB_ID, jobstore=ctx.api.JOBSTORE_MEMORY)
        restore = _restorable(ctx, sensor, ctx.snapshot_manager.get(SNAPSHOT_NAME))
        timed_name, timed_rows = _timed_rows(ctx, sensor)
        log_entry.add(Log.ENTRANCE, f"Applying `{timed_name}` rules")
        ctx.api.dispatch(m.squish_configs(restore, _to_configs(ctx, [*sensor.rules.enter, *timed_rows])), force=True, entry=log_entry)
    elif new == sensor.setting.inactive_event:
        ctx.api.dispatch(_to_configs(ctx, sensor.rules.inside, trigger=m.Trigger.SYSTEM), entry=log_entry)
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
def _run_max_on(sensor: SimpleNamespace, name: str, *, ctx: m.AppContext) -> None:
    entry = ctx.api.log(Log.ENTRANCE, f"`{name}` on for {sensor.setting.entrance_max_on}m: applying inside rules")
    ctx.api.dispatch(_to_configs(ctx, sensor.rules.inside, trigger=m.Trigger.SYSTEM), entry=entry)


@requires_ctx
def _run_trigger_sensor_off(sensor: SimpleNamespace, log_entry: m.LogEntry, *, ctx: m.AppContext) -> None:
    ctx.api.expire_presence(list(ctx.api.last_seen()))
    present = ctx.api.check_presence(silent=True)
    door_open = not present and _door_open(ctx, sensor)

    if present or door_open:
        ctx.api.dispatch(_to_configs(ctx, sensor.rules.present), entry=log_entry)
        msg = sensor.message.log_door_open if door_open else sensor.message.log_present
    elif any(s.content for s in ctx.api.capture_sounds().items):
        # Visitor left, pet still listening: restore the pre-visit state
        ctx.snapshot_manager.resume(SNAPSHOT_NAME, m.Configs(), log_entry)
        ctx.api.dispatch(_to_configs(ctx, sensor.rules.absent), entry=log_entry)
        msg = sensor.message.log_absent
    else:
        end = ctx.api.local_now() + timedelta(minutes=sensor.setting.snapshot)
        ctx.snapshot_manager.replace_config(SNAPSHOT_NAME, _to_configs(ctx, sensor.rules.shutdown), end, SNAPSHOT_NAME, log_entry)
        ctx.api.dispatch(_to_configs(ctx, sensor.rules.absent), entry=log_entry)
        msg = sensor.message.log_shutdown
    log_entry.add(Log.ENTRANCE, msg)


def battery_state(ctx: m.AppContext, sensor_ids: set[int]) -> list[m.DeviceStatus]:
    devices = ctx.api.device_states()
    return [
        m.DeviceStatus(
            name=d.name,
            details={
                "battery": m.BatteryLevel.from_fraction(battery, 100).value if battery is not None else None,
                "last_activity": d.last_activity,
            },
        )
        for device_id in sorted(sensor_ids)
        if (d := _sensor(devices, device_id)) is not None
        for battery in (d.attributes.get("battery"),)
    ]


def _door_open(ctx: m.AppContext, sensor: SimpleNamespace) -> bool:
    # An open entrance door means someone is around even if presence hasn't seen them.
    # A door never seen over MQTT reads as closed, falling back to the presence-only
    # decision like the old unreachable-hub path.
    device = _sensor(ctx.api.device_states(), sensor.setting.patio_door_id)
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
        ((name, rows) for (name, rows) in sensor.timed.items() if rows and in_window(rows[0])),
        ("(no window found)", ()),
    )


def _restorable(ctx: m.AppContext, sensor: SimpleNamespace, snapshot: m.SnapShot | None) -> m.Configs:
    # The snapshot is captured after the inside rule ran, so its state for those
    # lights is plugin-caused, not household state - don't replay it.
    if snapshot is None:
        return m.Configs()
    inside = {d for r in sensor.rules.inside for d in r.devices.all()}
    return m.Configs(*[c for c in snapshot.routine.items if c.what.one() not in inside])


def _to_configs(ctx: m.AppContext, rows: Sequence[Any], trigger: m.Trigger | None = None) -> m.Configs:
    return m.Configs(*[m.Config(r.devices, r.state, trigger=trigger) for r in rows])
