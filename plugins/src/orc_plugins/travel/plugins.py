from datetime import datetime, timedelta
from typing import Any

from apscheduler.schedulers.base import BaseScheduler
from apscheduler.triggers.date import DateTrigger
from orc_plugins.travel.dal.sqlite import Connection
from orc_plugins.travel.model import Plan, Runtime, TravelJob

from orc.model import AppContext
from orc.plugins import requires_ctx

_runtime: Runtime | None = None


def set_runtime(runtime: Runtime) -> None:
    global _runtime
    _runtime = runtime


def available_extras() -> list[str]:
    return [e.name for e in _runtime.extras] if _runtime else []


def place_names() -> list[str]:
    return [p.name for p in _runtime.places] if _runtime else []


def resolve_place(dest: str | None) -> str | None:
    for p in _runtime.places if _runtime and dest else ():
        if p.name == dest:
            return p.address
    return dest


def _target(rt: Runtime, job: TravelJob, tz: Any) -> tuple[datetime, str, str | None]:
    if job.iata:
        when = job.arrive if job.arrive.tzinfo else job.arrive.replace(tzinfo=tz)
        arrive, where, terminal = rt.flight.arrival(rt.aerodatabox_key, job.iata, when, job.airport, rt.settings.http_timeout)
    else:
        arrive, where, terminal = job.arrive, job.destination, None
    return (arrive if arrive.tzinfo else arrive.replace(tzinfo=tz)), where, terminal


def _reschedule(scheduler: BaseScheduler, job: TravelJob, when: datetime, tz: Any) -> None:
    from orc import api

    scheduler.add_job(
        run_job,
        DateTrigger(when, timezone=tz),
        args=(job,),
        id=job.summary,
        replace_existing=True,
        jobstore=api.JOBSTORE_DEFAULT,
    )


def leave_by(rt: Runtime, job: TravelJob, tz: Any, connection: Connection) -> Plan:
    arrive, where, terminal = _target(rt, job, tz)
    lead = rt.drive.drive_minutes(connection, rt.tomtom_key, rt.origin, where, rt.settings.http_timeout)
    lead += sum(e.minutes for e in rt.extras if e.name in job.extras)
    return Plan(arrive - timedelta(minutes=lead), where, terminal)


def schedule(scheduler: BaseScheduler, job: TravelJob, tz: Any) -> None:
    from orc import api

    # First poll a few seconds out, not at `now`: an execute-immediately job is briefly
    # absent from the jobstore while run_job runs, so a create-then-list would miss it.
    _reschedule(scheduler, job, api.local_now() + timedelta(seconds=5), tz)


@requires_ctx
def run_job(job: TravelJob, *, ctx: AppContext) -> None:
    assert _runtime is not None
    rt, tz, now = _runtime, ctx.config.settings.tz, ctx.api.local_now()
    plan = leave_by(rt, job, tz, ctx.api.connection)
    if now >= plan.leave_at:
        print(f"{job.summary}: head out now for {plan.where}{f' Terminal {plan.terminal}' if plan.terminal else ''}")
        return

    window_start = plan.leave_at - timedelta(hours=rt.settings.window_hours)
    next_check = window_start if now < window_start else now + timedelta(minutes=15)
    _reschedule(ctx.scheduler, job, min(next_check, plan.leave_at), tz)
