from datetime import timedelta
from typing import Any, NamedTuple

from apscheduler.triggers.date import DateTrigger

from orc import model as m
from orc.plugins import requires_ctx

JOB_ID = "react"
_COOLDOWN = timedelta(seconds=10)  # a (rule, device) won't re-fire within this window — breaks flapping loops

TRIGGERS = {"on": "switch", "off": "switch", "open": "contact", "closed": "contact"}

# per-rule last-fired times, created per setup() and passed in (never a module global)
type Cooldowns = dict[int, Any]


class Log(m.LogSourceEnum):
    REACT = "react"


class When(NamedTuple):
    device: m.DeviceEnum
    state: str | m.AcState | m.Playback


def _when_holds(ctx: m.AppContext, when: When | None) -> bool:
    if when is None:
        return True
    if isinstance(when.state, m.AcState):
        status = next((s for s in ctx.api.capture_acs().items if s.what is when.device), None)
        return status is not None and status.state is not None and status.state in when.state
    elif isinstance(when.state, m.Playback):
        sound = next((s for s in ctx.api.capture_sounds().items if s.what is when.device), None)
        return sound is not None and sound.playback is when.state
    else:
        target = str(when.device.value)
        found = next((s for s in ctx.api.device_states() if str(s.id) == target or s.name == target), None)
        return found is not None and found.attributes.get(TRIGGERS[when.state]) == when.state


def _on_event(
    ctx: m.AppContext,
    rules: list[tuple[int, Any, dict[str, m.DeviceEnum]]],
    cooldowns: Cooldowns,
    device: m.DeviceState,
    attribute: str,
    old: Any,
    new: Any,
) -> None:
    if old == new:
        return
    for index, rule, by_id in rules:
        if attribute != rule.attribute:
            continue
        source = by_id.get(str(device.id)) or by_id.get(device.name)
        if source is None:
            continue
        if new == rule.state:
            _trigger(ctx, index, rule, source, device.name, cooldowns)
        else:
            _cancel(ctx, index, source)


def _targets(what: m.Devices) -> str:
    return ", ".join(f"`{d.label or d.name}`" for d in what.all())


def _unmet(when: When) -> str:
    if isinstance(when.state, m.AcState):
        state = (when.state.name or "").lower()
    elif isinstance(when.state, m.Playback):
        state = when.state.value
    else:
        state = when.state
    return f"`{when.device.label or when.device.name}` is not {state}"


def _trigger(ctx: m.AppContext, index: int, rule: Any, source: m.DeviceEnum, name: str, cooldowns: Cooldowns) -> None:
    now = ctx.api.local_now()
    if (last := cooldowns.get(index)) is not None and now - last < _COOLDOWN:
        ctx.api.log(Log.REACT, f"`{name}` {rule.state} — skipped, rule fired {int((now - last).total_seconds())}s ago (cooldown)")
        return
    what = rule.target or m.Devices(source)
    if rule.delay is None:
        if rule.when and not _when_holds(ctx, rule.when):
            ctx.api.log(Log.REACT, f"`{name}` {rule.state} — skipped, {_unmet(rule.when)}")
        else:
            cooldowns[index] = now
            entry = ctx.api.log(Log.REACT, f"`{name}` {rule.state} → set {_targets(what)} {rule.action}")
            _apply(ctx, what, rule.action, entry)
    else:
        cooldowns[index] = now
        ctx.scheduler.add_job(
            _run_react,
            DateTrigger(ctx.api.local_now() + timedelta(minutes=rule.delay), timezone=ctx.config.settings.tz),
            name=f"React {name}",
            id=f"{JOB_ID}-{index}-{source.value}",
            replace_existing=True,
            jobstore=ctx.api.JOBSTORE_MEMORY,
            args=(what, name, rule.state, rule.action, rule.delay, rule.when),
        )


def _cancel(ctx: m.AppContext, index: int, source: m.DeviceEnum) -> None:
    job_id = f"{JOB_ID}-{index}-{source.value}"
    if ctx.scheduler.get_job(job_id, jobstore=ctx.api.JOBSTORE_MEMORY):
        ctx.scheduler.remove_job(job_id, jobstore=ctx.api.JOBSTORE_MEMORY)


def _apply(ctx: m.AppContext, what: m.Devices, action: Any, entry: m.LogEntry) -> None:
    ctx.api.dispatch(m.Configs(m.Config(what, action, trigger=m.Trigger.SYSTEM)), entry=entry)


@requires_ctx
def _run_react(what: m.Devices, name: str, state: str, action: Any, minutes: int, when: When | None, *, ctx: m.AppContext) -> None:
    if when and not _when_holds(ctx, when):
        ctx.api.log(Log.REACT, f"`{name}` {state} {minutes}m ago — skipped, {_unmet(when)}")
    else:
        entry = ctx.api.log(Log.REACT, f"`{name}` {state} {minutes}m ago → set {_targets(what)} {action}")
        _apply(ctx, what, action, entry)
