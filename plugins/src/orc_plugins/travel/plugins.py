from datetime import datetime, time, timedelta
from typing import Any

from apscheduler.schedulers.base import BaseScheduler
from apscheduler.triggers.date import DateTrigger
from orc_plugins.travel.dal.sqlite import Connection
from orc_plugins.travel.model import Arrival, Extra, Log, Runtime, Schedule, TravelJob

from orc.model import AppContext
from orc.plugins import requires_ctx

_runtime: Runtime | None = None


def set_runtime(runtime: Runtime) -> None:
    global _runtime
    _runtime = runtime


def available_extras() -> list[Extra]:
    return list(_runtime.extras) if _runtime else []


def place_names() -> list[str]:
    return [p.name for p in _runtime.places] if _runtime else []


def resolve_place(dest: str | None) -> str | None:
    for p in _runtime.places if _runtime and dest else ():
        if p.name == dest:
            return p.address
    return dest


def validate_place(connection: Any, address: str) -> None:
    if _runtime is None:
        return
    try:
        found = _runtime.drive.geocode(connection, _runtime.tomtom_key, address, _runtime.settings.http_timeout)
    except Exception as exc:
        raise ValueError(f"Couldn't check '{address}' right now — the address lookup service is unavailable.") from exc
    if not found:
        raise ValueError(f"'{address}' isn't a recognized address — check it and try again.")


def _arrival(rt: Runtime, job: TravelJob, tz: Any) -> Arrival:
    if job.iata:
        when = job.arrive if job.arrive.tzinfo else job.arrive.replace(tzinfo=tz)
        arrive, where, terminal = rt.flight.arrival(rt.aerodatabox_key, job.iata, when, job.airport, rt.settings.http_timeout)
    else:
        arrive, where, terminal = job.arrive, job.destination, None
    return Arrival(arrive if arrive.tzinfo else arrive.replace(tzinfo=tz), where, terminal)


def _reschedule(scheduler: BaseScheduler, job: TravelJob, when: datetime, tz: Any) -> None:
    from orc import api

    scheduler.add_job(
        run_job,
        DateTrigger(when, timezone=tz),
        args=(job,),
        id=f"{job.summary}@{job.arrive.isoformat()}",
        replace_existing=True,
        jobstore=api.JOBSTORE_DEFAULT,
    )


def schedule(scheduler: BaseScheduler, job: TravelJob, tz: Any) -> None:
    from orc import api

    # First poll a few seconds out, not at `now`: an execute-immediately job is briefly
    # absent from the jobstore while run_job runs, so a create-then-list would miss it.
    _reschedule(scheduler, job, api.local_now() + timedelta(seconds=5), tz)


def next_run(rt: Runtime, job: TravelJob, now: datetime, arrival: Arrival | None, connection: Connection) -> Schedule:
    """The leave time (None until the leave day) and when to next fire: midnight of
    the leave day until it arrives, then 2 hours before the leave, then every 10
    minutes, then None once within 10 minutes of the leave."""
    if arrival is None:
        return Schedule(None, datetime.combine(job.arrive.astimezone(now.tzinfo).date(), time.min, tzinfo=now.tzinfo))
    lead = rt.drive.drive_minutes(connection, rt.tomtom_key, rt.origin, arrival.where, rt.settings.http_timeout)
    lead += sum(e.minutes for e in rt.extras if e.name in job.extras)
    lead += rt.settings.buffer_minutes
    leave_at = arrival.when - timedelta(minutes=lead)
    remaining = leave_at - now
    if remaining <= timedelta(minutes=10):
        return Schedule(leave_at, None)
    if remaining <= timedelta(hours=2):
        return Schedule(leave_at, now + timedelta(minutes=10))
    return Schedule(leave_at, leave_at - timedelta(hours=2))


@requires_ctx
def run_job(job: TravelJob, *, ctx: AppContext) -> None:
    assert _runtime is not None
    rt, tz, now = _runtime, ctx.config.settings.tz, ctx.api.local_now()
    arrival = _arrival(rt, job, tz) if now.date() == job.arrive.astimezone(tz).date() else None
    sched = next_run(rt, job, now, arrival, ctx.api.connection)
    job.leave_at = sched.leave_at
    if sched.next_fire is not None:
        _reschedule(ctx.scheduler, job, sched.next_fire, tz)
        return
    assert arrival is not None
    drive = rt.drive.drive_minutes(ctx.api.connection, rt.tomtom_key, rt.origin, arrival.where, rt.settings.http_timeout)
    parts = [f"{drive} min drive"] + [f"{e.minutes} min {e.name}" for e in rt.extras if e.name in job.extras]
    detail = " + ".join(parts)
    ctx.api.log(Log.TRAVEL, f"{job.summary}: {detail}{f' (Terminal {arrival.terminal})' if arrival.terminal else ''}")
