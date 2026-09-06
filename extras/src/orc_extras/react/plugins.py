from datetime import timedelta
from enum import StrEnum
from typing import Any, NamedTuple

from apscheduler.triggers.date import DateTrigger

from orc import model as m
from orc.plugins import requires_ctx

JOB_ID = "react"


class Log(m.LogSourceEnum):
    REACT = "react"


class AcMode(StrEnum):
    COOL = "cool"
    FAN_ONLY = "fan_only"
    ECON = "econ"
    DRY = "dry"


class AcAction(NamedTuple):
    mode: AcMode
    fan: str
    temp: int

    def __str__(self) -> str:
        return f"{self.mode}:{self.fan}:{self.temp}"


def _on_event(
    ctx: m.AppContext, rules: list[tuple[int, Any, dict[str, m.DeviceEnum]]], device: m.DeviceState, attribute: str, old: Any, new: Any
) -> None:
    if old == new:
        return
    for index, rule, by_id in rules:
        if attribute != rule.attribute:
            continue
        member = by_id.get(str(device.id)) or by_id.get(device.name)
        if member is None:
            continue
        if new == rule.state:
            _trigger(ctx, index, rule, member, device.name)
        else:
            _cancel(ctx, index, member)


def _trigger(ctx: m.AppContext, index: int, rule: Any, member: m.DeviceEnum, name: str) -> None:
    if rule.delay is None:
        entry = ctx.api.log(Log.REACT, f"`{name}` {rule.state} — {rule.action}")
        _apply(ctx, member, rule.action, entry)
    else:
        ctx.scheduler.add_job(
            _run_react,
            DateTrigger(ctx.api.local_now() + timedelta(minutes=rule.delay), timezone=ctx.config.settings.tz),
            name=f"React {name}",
            id=f"{JOB_ID}-{index}-{member.value}",
            replace_existing=True,
            jobstore=ctx.api.JOBSTORE_MEMORY,
            args=(member, name, rule.action, rule.delay),
        )


def _cancel(ctx: m.AppContext, index: int, member: m.DeviceEnum) -> None:
    job_id = f"{JOB_ID}-{index}-{member.value}"
    if ctx.scheduler.get_job(job_id, jobstore=ctx.api.JOBSTORE_MEMORY):
        ctx.scheduler.remove_job(job_id, jobstore=ctx.api.JOBSTORE_MEMORY)


def _apply(ctx: m.AppContext, what: m.DeviceEnum, action: Any, entry: m.LogEntry) -> None:
    if isinstance(action, AcAction):
        for ac in ctx.config.registry.devices["AC"].cls:
            ctx.api.ac_command(ac, m.ON, action.mode, action.fan, action.temp)
    else:
        ctx.api.dispatch(m.Configs(m.Config(m.Devices(what), action, trigger=m.Trigger.SYSTEM)), entry=entry)


@requires_ctx
def _run_react(what: m.DeviceEnum, name: str, action: Any, minutes: int, *, ctx: m.AppContext) -> None:
    entry = ctx.api.log(Log.REACT, f"`{name}` on {minutes}m — {action}")
    _apply(ctx, what, action, entry)
