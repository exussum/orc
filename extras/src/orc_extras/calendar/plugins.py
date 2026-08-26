import dataclasses
from datetime import datetime, timedelta
from itertools import chain, islice
from typing import Any

from apscheduler.events import EVENT_ALL_JOBS_REMOVED
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from orc import model as m
from orc.plugins import requires_ctx
from orc_extras.calendar.dal.interfaces import FeedService

WARNING = "warning"
ALARM = "alarm"
CRON_ID = "cal-cron"


class Log(m.LogSourceEnum):
    CALENDAR = "calendar"


@dataclasses.dataclass
class CalendarEvent:
    uuid: str
    summary: str
    datetime: datetime
    type: str

    @staticmethod
    def from_cal(cal: Any, feed: str, type: str, offset: timedelta, tz: Any) -> CalendarEvent:
        return CalendarEvent(
            feed + " " + cal.uid.to_ical().decode() + " " + type,
            cal.summary.to_ical().decode("utf-8"),
            cal.start.astimezone(tz) + offset,
            type,
        )


@dataclasses.dataclass
class CalendarJob:
    event_type: str
    summary: str


def schedule_cron(ctx: m.AppContext, backend: FeedService, settings: Any, feeds: list[tuple[str, str]]) -> None:
    # api.rebuild_jobs wipes every jobstore and re-adds only core crons; the
    # listener puts this one back whenever that happens.
    ctx.scheduler.add_listener(lambda event: _add_cron(ctx, backend, settings, feeds), EVENT_ALL_JOBS_REMOVED)
    _add_cron(ctx, backend, settings, feeds)


def _add_cron(ctx: m.AppContext, backend: FeedService, settings: Any, feeds: list[tuple[str, str]]) -> None:
    ctx.scheduler.add_job(
        _rebuild,
        CronTrigger.from_crontab(settings.cron, timezone=ctx.config.settings.tz),
        args=(backend, settings, feeds),
        replace_existing=True,
        id=CRON_ID,
        name="Calendar Cron",
        jobstore=ctx.api.JOBSTORE_MEMORY,
    )


@requires_ctx
def _rebuild(backend: FeedService, settings: Any, feeds: list[tuple[str, str]], *, ctx: m.AppContext) -> None:
    now: datetime = ctx.api.local_now()
    if ctx.api.calculate_theme(now.date()) != m.THEME_WORK_DAY:
        return

    tz = ctx.config.settings.tz
    events_by_id: dict[str, CalendarEvent] = {}
    for name, secret in feeds:
        url = ctx.config.secrets[secret]
        events = list(
            islice(backend.fetch_ical(now, timedelta(hours=settings.window_hours), url, settings.http_timeout), settings.max_events)
        )
        warning_events = (CalendarEvent.from_cal(e, name, WARNING, timedelta(minutes=-settings.warning_minutes), tz) for e in events)
        alarm_events = (CalendarEvent.from_cal(e, name, ALARM, timedelta(), tz) for e in events)
        events_by_id.update({e.uuid: e for e in chain(alarm_events, warning_events)})

    for job in ctx.api.fetch_jobs_by_type(CalendarJob):
        if job.id not in events_by_id:
            ctx.scheduler.remove_job(job.id)

    for id, event in events_by_id.items():
        ctx.scheduler.add_job(
            _run_event,
            DateTrigger(event.datetime, timezone=tz),
            args=(CalendarJob(event.type, event.summary),),
            replace_existing=True,
            id=id,
            name=event.summary,
            jobstore=ctx.api.JOBSTORE_MEMORY,
        )


@requires_ctx
def _run_event(job: CalendarJob, *, ctx: m.AppContext) -> None:
    if job.event_type == WARNING:
        ctx.api.play_alert(ctx.api.DEFAULT_ALERT_PATH)
    else:
        ctx.api.log(Log.CALENDAR, job.summary)
        ctx.api.play_text(job.summary)
