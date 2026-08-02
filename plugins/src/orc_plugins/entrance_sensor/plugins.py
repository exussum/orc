from __future__ import annotations

from datetime import timedelta
from enum import Enum
from functools import partial
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Sequence

from apscheduler.triggers.date import DateTrigger

from orc.plugins import PluginCtx, build_ctx, plugin_config, requires_ctx

if TYPE_CHECKING:
    from orc import model as m

SNAPSHOT_NAME = "entrance_sensor"
JOB_ID = "trigger-sensor"
TRIGGER_MSG = "Entrance sensor triggered"


@plugin_config(
    "entrance_sensor",
    schema={
        "Settings": ("Key", "Value"),
        "Messages": ("Log", "Message"),
        "Rules": ("Trigger", "Device", "State"),
        "Timed": ("Name", "Start", "Stop", "Device", "State"),
    },
)
def setup(ctx: PluginCtx, sensor: SimpleNamespace) -> None:
    ids = {sensor.entrance_id, sensor.patio_door_id}
    ctx.api.add_listener(partial(_on_sensor_event, ctx, sensor, ids))
    ctx.api.add_state_provider("Entrance Sensors", partial(battery_state, ctx, ids))


def _on_sensor_event(
    ctx: PluginCtx, sensor: SimpleNamespace, sensor_ids: set[int], device: m.DeviceState, attribute: str, old: Any, new: Any
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
    plugin_ctx = build_ctx(ctx)
    if new == sensor.active_event:
        if plugin_ctx.scheduler.get_job(JOB_ID, jobstore=plugin_ctx.api.JOBSTORE_MEMORY):
            plugin_ctx.scheduler.remove_job(JOB_ID, jobstore=plugin_ctx.api.JOBSTORE_MEMORY)
        restore = _restorable(plugin_ctx, sensor, plugin_ctx.snapshot_manager.get(SNAPSHOT_NAME))
        timed_name, timed_rows = _timed_rows(plugin_ctx, sensor)
        log_entry.add(plugin_ctx.model.LogSource.PLUGIN, f"Applying `{timed_name}` rules")
        plugin_ctx.api.dispatch(
            plugin_ctx.model.squish_configs(restore, _to_configs(plugin_ctx, [*sensor.rules.enter, *timed_rows])), force=True
        )
    elif new == sensor.inactive_event:
        plugin_ctx.api.dispatch(_to_configs(plugin_ctx, sensor.rules.inside, trigger=plugin_ctx.model.Trigger.SYSTEM))
        plugin_ctx.scheduler.add_job(
            _run_trigger_sensor_off,
            DateTrigger(plugin_ctx.api.local_now() + timedelta(minutes=sensor.cleanup_delay_minutes), timezone=plugin_ctx.config.tz),
            name="Trigger Sensor",
            id=JOB_ID,
            replace_existing=True,
            jobstore=plugin_ctx.api.JOBSTORE_MEMORY,
            args=(sensor, log_entry),
        )


@requires_ctx
def _run_trigger_sensor_off(sensor: SimpleNamespace, log_entry: m.LogEntry, *, ctx: m.AppContext) -> None:
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
    log_entry.add(plugin_ctx.model.LogSource.PLUGIN, msg)


def battery_state(ctx: PluginCtx, sensor_ids: set[int]) -> list[dict[str, Any]]:
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


def _door_open(ctx: PluginCtx, sensor: SimpleNamespace) -> bool:
    # An open entrance door means someone is around even if presence hasn't seen them.
    # A door never seen over MQTT reads as closed, falling back to the presence-only
    # decision like the old unreachable-hub path.
    device = _sensor(ctx.api.device_states(), sensor.patio_door_id)
    return device is not None and device.attributes.get("contact") == "open"


def _sensor(devices: Sequence[m.DeviceState], device_id: int) -> m.DeviceState | None:
    return next((d for d in devices if d.id == device_id), None)


def _timed_rows(ctx: PluginCtx, sensor: SimpleNamespace) -> tuple[str, Sequence[Any]]:
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


def _restorable(ctx: PluginCtx, sensor: SimpleNamespace, snapshot: m.SnapShot | None) -> m.Configs:
    # The snapshot is captured after the inside rule ran, so its state for those
    # lights is plugin-caused, not household state - don't replay it.
    if snapshot is None:
        return ctx.model.Configs()
    inside = {d for r in sensor.rules.inside for d in ((r.device,) if isinstance(r.device, Enum) else r.device)}
    return ctx.model.Configs(*[c for c in snapshot.routine.items if c.what not in inside])


def _to_configs(ctx: PluginCtx, rows: Sequence[Any], trigger: m.Trigger | None = None) -> m.Configs:
    return ctx.model.Configs(*[ctx.model.Config(r.device, r.state, trigger=trigger) for r in rows])
