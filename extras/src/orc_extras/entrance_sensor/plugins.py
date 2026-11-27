from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, NamedTuple

from orc_engine import model as em

import orc_extras.entrance_sensor
from orc import model as m

SNAPSHOT_NAME = "entrance_sensor"
CLEANUP_JOB_ID = "trigger-sensor"
TRIGGER_MSG = "Entrance sensor triggered"


class Visit(NamedTuple):
    entry: m.LogEntry
    present_before: set[str]


class Log(m.LogSourceEnum):
    ENTRANCE = "entrance"


def _on_event(ctx: m.AppContext, sensor: SimpleNamespace, status: m.Status) -> None:
    device, attribute, old, new = status.device, status.attribute, status.old, status.new
    if _entrance_motion_changed(sensor, device, attribute, old, new):
        # The listener runs on the mqtt network thread, where a publish is only
        # queued until the callback returns: dispatching here holds the light
        # command behind the chromecast I/O the same dispatch triggers. Run on
        # the scheduler's coordinator; None grace so a busy coordinator delays, never drops.

        previous = ctx.plugin_state[orc_extras.entrance_sensor]
        if previous:
            log_entry = previous.entry
            ctx.log.attach(log_entry, trigger)
        else:
            log_entry = ctx.log.write(TRIGGER_MSG, replace(trigger, log_source=Log.ENTRANCE))
            ctx.log.open_row(log_entry)
        ctx.plugin_state[orc_extras.entrance_sensor] = Visit(
            log_entry, previous.present_before if previous else set(ctx.api.present_names())
        )
        ctx.scheduler.now(_run_motion, (sensor, new, log_entry.trigger), name="Entrance Motion")


def _entrance_motion_changed(sensor: SimpleNamespace, device: m.Device, attribute: str, old: Any, new: Any) -> bool:
    return (
        attribute == "motion"
        and old != new
        and device.id == sensor.setting.entrance.value
        and new in (sensor.setting.active_event, sensor.setting.inactive_event)
    )


def _run_motion(payload: tuple[SimpleNamespace, Any, m.Trigger], *, ctx: m.AppContext) -> None:
    sensor, new, trigger = payload
    if new == sensor.setting.active_event:
        ctx.scheduler.pause_queue(m.Queue.IOT)
        ctx.api.pause_presence()
        ctx.api.delete_all_presence(trigger)
        restore = _restorable(ctx, sensor, ctx.engine.pop_snapshot(SNAPSHOT_NAME))
        timed_name, timed_commands = _timed_commands(ctx, sensor)
        ctx.log.write(f"Applying `{timed_name}` rules", trigger)
        ctx.api.dispatch((*restore, *timed_commands), force=True, trigger=trigger)
    else:
        ctx.api.run_action(sensor.rules.inside, trigger)
    ctx.scheduler.once(
        _run_trigger_sensor_off,
        ctx.api.local_now() + timedelta(minutes=sensor.setting.cleanup_delay_minutes),
        (sensor, trigger),
        id=CLEANUP_JOB_ID,
        name="Entrance Cleanup",
    )


def _run_trigger_sensor_off(payload: tuple[SimpleNamespace, m.Trigger], *, ctx: m.AppContext) -> None:
    sensor, trigger = payload
    visit = ctx.plugin_state[orc_extras.entrance_sensor]
    ctx.plugin_state[orc_extras.entrance_sensor] = None
    present = ctx.api.check_presence(trigger, probe=True)
    ctx.api.resume_presence(trigger)
    ctx.scheduler.release_queue(m.Queue.IOT)
    people = present - {sensor.setting.listener}
    door_open = not people and _door_open(ctx, sensor)

    if people or door_open:
        ctx.api.run_action(sensor.rules.present, trigger)
        ctx.log.write(sensor.message.log_door_open if door_open else sensor.message.log_present, trigger)
    elif sensor.setting.listener in present:
        # Visitor left, the listener stayed: restore the pre-visit state
        ctx.api.restore_scene(SNAPSHOT_NAME, (), trigger)
        ctx.api.run_action(sensor.rules.absent, trigger)
        ctx.log.write(sensor.message.log_absent, trigger)
    else:
        end = ctx.api.local_now() + timedelta(minutes=sensor.setting.snapshot)
        ctx.api.override_scene(SNAPSHOT_NAME, ctx.config.ad_hoc_routines[sensor.rules.shutdown].commands, end, SNAPSHOT_NAME, trigger)
        ctx.log.write(sensor.message.log_shutdown, trigger)
        if not visit.present_before and not present:
            ctx.log.write(sensor.message.log_nobody, trigger, notification=m.Notification(("entrance",), "/presence/"))
    ctx.log.close_row(visit.entry)


def _door_open(ctx: m.AppContext, sensor: SimpleNamespace) -> bool:
    # An open entrance door means someone is around even if presence hasn't seen them.
    # A door never seen over MQTT reads as closed, falling back to the presence-only
    # decision like the old unreachable-hub path.
    device = ctx.api.device_state(sensor.setting.patio_door.value)
    return device is not None and device.attributes.get("contact") == "open"


def _timed_commands(ctx: m.AppContext, sensor: SimpleNamespace) -> tuple[str, m.Commands]:
    for outcome in ctx.engine.evaluate(sensor.timed, read=ctx.api.reader(), force=True):
        match outcome:
            case em.Report(em.Rule(name=name), commands) if commands:
                return name, commands
    return "(no window found)", ()


def _restorable(ctx: m.AppContext, sensor: SimpleNamespace, snapshot: m.SnapShot | None) -> m.Commands:
    # The snapshot is captured after the inside rule ran, so its state for those
    # lights is plugin-caused, not household state - don't replay it.
    if snapshot is None:
        return ()
    inside = {d for c in ctx.config.ad_hoc_routines[sensor.rules.inside].commands for d in c.subject.all()}
    return tuple(c for c in snapshot.routine if c.subject.one() not in inside)
