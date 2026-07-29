from __future__ import annotations

from datetime import timedelta
from enum import Enum
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Sequence

from apscheduler.triggers.date import DateTrigger
from orc_plugins.entrance_sensor import dal

from orc.plugins import PluginCtx, build_ctx, plugin_config, requires_ctx

if TYPE_CHECKING:
    from orc import model as m

SNAPSHOT_NAME = "entrance_sensor"
JOB_ID = "trigger-sensor"

_sensor_ids: set[int] = set()  # device ids this plugin watches, wired at startup


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


@plugin_config("entrance_sensor", schema={"Settings": ("Key", "Value")})
def start(ctx: PluginCtx, sensor: SimpleNamespace) -> None:
    _sensor_ids.update((sensor.entrance_id, sensor.patio_door_id))
    ctx.api.add_listener(_on_sensor_event)


def _on_sensor_event(device: m.DeviceState, attribute: str, old: Any, new: Any) -> None:
    """Central-listener consumer: record watched sensors into the dal store and log
    critical battery reports."""
    from orc import api
    from orc import model as m

    if device.id not in _sensor_ids:
        return
    dal.record(device)
    if attribute != "battery":
        return
    level = m.BatteryLevel.from_fraction(new, 100)
    if level.is_critical:
        api.log(api.local_now(), m.LogSource.PLUGIN, f"Low battery on {device.name} ({level.value})")


def battery_state() -> list[dict[str, Any]]:
    """Per-sensor battery rows for core's generic state renderer (needs a "name" key).
    Device names come from the hub's documents; ids without a document yet are skipped."""
    return [
        {"name": s.name, "battery": s.battery.value if s.battery is not None else None, "last_activity": s.last_activity}
        for device_id in sorted(_sensor_ids)
        if (s := dal.get(device_id)) is not None
    ]


def _door_open(ctx: PluginCtx, sensor: SimpleNamespace) -> bool:
    # An open entrance door means someone is around even if presence hasn't seen them.
    # A door never seen over MQTT reads as closed, falling back to the presence-only
    # decision like the old unreachable-hub path.
    state = dal.get(sensor.patio_door_id)
    return state is not None and state.attributes.get("contact") == "open"


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
