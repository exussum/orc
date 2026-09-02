from datetime import timedelta
from typing import Any

from apscheduler.triggers.date import DateTrigger

from orc import model as m
from orc.plugins import requires_ctx

JOB_ID = "max-on"


class Log(m.LogSourceEnum):
    MAX_ON = "max_on"


def _on_switch_event(
    ctx: m.AppContext, minutes: int, by_id: dict[int, m.DeviceEnum], device: m.DeviceState, attribute: str, old: Any, new: Any
) -> None:
    if attribute != "switch" or device.id not in by_id or old == new:
        return
    job_id = f"{JOB_ID}-{device.id}"
    if new == m.ON:
        ctx.scheduler.add_job(
            _run_max_on,
            DateTrigger(ctx.api.local_now() + timedelta(minutes=minutes), timezone=ctx.config.settings.tz),
            name=f"Max On {device.name}",
            id=job_id,
            replace_existing=True,
            jobstore=ctx.api.JOBSTORE_MEMORY,
            args=(by_id[device.id], device.name, minutes),
        )
    elif ctx.scheduler.get_job(job_id, jobstore=ctx.api.JOBSTORE_MEMORY):
        ctx.scheduler.remove_job(job_id, jobstore=ctx.api.JOBSTORE_MEMORY)


@requires_ctx
def _run_max_on(what: m.DeviceEnum, name: str, minutes: int, *, ctx: m.AppContext) -> None:
    entry = ctx.api.log(Log.MAX_ON, f"`{name}` on {minutes}m — turning off")
    ctx.api.dispatch(m.Configs(m.Config(m.Devices(what), m.OFF, trigger=m.Trigger.SYSTEM)), entry=entry)
