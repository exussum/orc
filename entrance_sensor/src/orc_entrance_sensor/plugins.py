from __future__ import annotations

from datetime import timedelta

from apscheduler.triggers.date import DateTrigger

from orc.plugins import build_ctx, plugin_config, requires_ctx

SNAPSHOT_NAME = "entrance_sensor"


@plugin_config(
    "entrance_sensor",
    schema={
        "Settings": ("Key", "Value"),
        "Messages": ("Log", "Message"),
        "Day": ("Trigger", "Device", "State"),
        "Night": ("Trigger", "Device", "State"),
    },
)
def trigger_sensor(ctx, sensor, device_id, event):
    if int(device_id) != sensor.entrance_id:
        return

    hour = ctx.api.local_now().hour
    daytime = sensor.day_start <= hour < sensor.day_end
    phase = sensor.day if daytime else sensor.night

    if event == sensor.active_event:
        snapshot = ctx.snapshot_manager.get(SNAPSHOT_NAME)
        items = (snapshot.routine,) if snapshot else ()
        ctx.api.execute(ctx.model.squish_configs(*items, _to_configs(ctx, [*phase.entrance_light_on, *phase.entrance_config])))
    elif event == sensor.inactive_event:
        ctx.api.execute(_to_configs(ctx, phase.entrance_light_off))
        ctx.scheduler.add_job(
            _run_trigger_sensor_off,
            DateTrigger(ctx.api.local_now() + timedelta(minutes=sensor.cleanup_delay_minutes), timezone=ctx.config.tz),
            name="Trigger Sensor",
            id="trigger-sensor",
            replace_existing=True,
            jobstore=ctx.api.JOBSTORE_MEMORY,
            args=(sensor,),
        )


@requires_ctx
def _run_trigger_sensor_off(sensor, *, ctx):
    plugin_ctx = build_ctx(ctx)
    hour = plugin_ctx.api.local_now().hour
    daytime = sensor.day_start <= hour < sensor.day_end
    phase = sensor.day if daytime else sensor.night

    plugin_ctx.api.expire_presence(list(plugin_ctx.api.last_seen()))
    present = plugin_ctx.api.check_presence(ctx=ctx)

    if not daytime:
        plugin_ctx.api.execute(_to_configs(plugin_ctx, phase.after_hours))
        msg = sensor.log_after_hours
    elif present:
        plugin_ctx.api.execute(_to_configs(plugin_ctx, phase.after_hours))
        msg = sensor.log_present
    elif any(s.content for s in plugin_ctx.api.capture_sounds().items):
        plugin_ctx.api.execute(_to_configs(plugin_ctx, phase.core_hours))
        msg = sensor.log_core_hours
    else:
        end = plugin_ctx.api.local_now() + timedelta(minutes=sensor.snapshot)
        plugin_ctx.snapshot_manager.replace_config(SNAPSHOT_NAME, _to_configs(plugin_ctx, phase.shutdown), end)
        plugin_ctx.api.execute(_to_configs(plugin_ctx, phase.core_hours))
        msg = sensor.log_shutdown
    plugin_ctx.api.log(plugin_ctx.api.local_now(), plugin_ctx.model.LogSource.SYSTEM, msg)


def _to_configs(ctx, rows):
    return ctx.model.Configs(*[ctx.model.Config(r.device, r.state) for r in rows])
