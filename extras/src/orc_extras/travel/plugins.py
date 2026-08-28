import re
from datetime import datetime, time, timedelta
from typing import Any

from apscheduler.schedulers.base import BaseScheduler
from apscheduler.triggers.date import DateTrigger

from orc.model import AppContext
from orc.plugins import requires_ctx
from orc_extras.travel.dal.sqlite import Connection
from orc_extras.travel.model import Arrival, Extra, Log, Runtime, Schedule, TravelJob

_runtime: Runtime | None = None


def set_runtime(runtime: Runtime) -> None:
    global _runtime
    _runtime = runtime


_FLIGHT_RE = re.compile(r"^(?=.*[A-Za-z])[A-Za-z0-9]{2,3}\s?\d{1,4}$")


def is_flight(value: str) -> bool:
    return bool(_FLIGHT_RE.match(value.strip()))


def available_extras() -> list[Extra]:
    return list(_runtime.extras) if _runtime else []


def place_names() -> list[str]:
    return [p.name for p in _runtime.places] if _runtime else []


def resolve_place(dest: str | None) -> str | None:
    for p in _runtime.places if _runtime and dest else ():
        if p.name == dest:
            return p.address
    return dest


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
    if remaining < timedelta(0):
        return Schedule(leave_at, None, late=True, eta=now + timedelta(minutes=lead))
    if remaining <= timedelta(minutes=10):
        return Schedule(leave_at, None)
    if remaining <= timedelta(hours=2):
        return Schedule(leave_at, now + timedelta(minutes=10))
    return Schedule(leave_at, leave_at - timedelta(hours=2))


def evaluate(job: TravelJob, tz: Any, now: datetime, connection: Connection) -> tuple[Arrival | None, Schedule]:
    """Run a live aviation or TomTom lookup and compute the resulting Schedule in one pass,
    so a bad flight number or an unroutable destination is caught immediately instead of
    failing invisibly in the background. Flight data isn't available until the day of
    arrival, so a future-dated flight gets no arrival check yet (Schedule(None, midnight))."""
    assert _runtime is not None
    rt = _runtime
    if job.iata and now.date() != job.arrive.astimezone(tz).date():
        arrival = None
    else:
        try:
            arrival = _arrival(rt, job, tz)
        except Exception as exc:
            raise ValueError(f"Couldn't verify flight {job.iata}: {exc}") from exc
    try:
        sched = next_run(rt, job, now, arrival, connection)
    except Exception as exc:
        raise ValueError(f"Couldn't get a travel time to '{arrival.where if arrival else job.destination}': {exc}") from exc
    return arrival, sched


@requires_ctx
def run_job(job: TravelJob, *, ctx: AppContext) -> None:
    assert _runtime is not None
    tz, now = ctx.config.settings.tz, ctx.api.local_now()
    arrival, sched = evaluate(job, tz, now, ctx.api.connection)
    if arrival is not None:
        job.arrive = arrival.when
    job.leave_at, job.late, job.eta = sched.leave_at, sched.late, sched.eta
    if sched.next_fire is not None:
        _reschedule(ctx.scheduler, job, sched.next_fire, tz)
        return
    assert arrival is not None
    if job.iata:
        target = arrival.where + (f", Terminal {arrival.terminal}" if arrival.terminal else "")
    else:
        target = job.place or job.destination
    if job.late and sched.eta:
        eta_str = sched.eta.astimezone(tz).strftime("%I:%M %p")
        message = f"You're running late for {target}. Leaving now, you'll arrive around {eta_str}."
    else:
        message = f"Time to leave for {target}."
    ctx.api.log(Log.TRAVEL, message)
    ctx.api.announce(message)
