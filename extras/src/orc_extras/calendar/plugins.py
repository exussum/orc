import dataclasses
import re
from datetime import datetime, timedelta
from itertools import chain, islice
from typing import Any, NamedTuple

from yarl import URL

from orc import model as m
from orc.plugins import requires_ctx
from orc_extras.calendar.dal.interfaces import FeedService

WARNING = "warning"
ALARM = "alarm"
CRON_ID = "cal-cron"
CHOOSER_URL = URL("/api/calendar/choose/events")
GOOGLE_DAY_URL = URL("https://calendar.google.com/calendar/r/day")
_LINK_FIELDS = ("X-GOOGLE-CONFERENCE", "X-MICROSOFT-SKYPETEAMSMEETINGURL", "URL", "LOCATION", "DESCRIPTION")
_LINK = re.compile(r"""https?://([\w-]+\.)*(meet\.google\.com|zoom\.us|teams\.microsoft\.com)/[^\s<>"']*""")
_ADDRESS = re.compile(r"^(?!.*://)(?=.*\d)(?=.*,)")


class Log(m.LogSourceEnum):
    CALENDAR = "calendar"


class EventDestination(NamedTuple):
    summary: str
    url: str
    address: str


@dataclasses.dataclass
class CalendarEvent:
    uuid: str
    summary: str
    start: datetime
    end: datetime
    type: str
    url: str
    address: str

    @staticmethod
    def from_cal(cal: Any, feed: str, type: str, offset: timedelta, tz: Any) -> CalendarEvent:
        return CalendarEvent(
            feed + " " + cal.uid.to_ical().decode() + " " + cal.start.astimezone(tz).isoformat() + " " + type,
            cal.summary.to_ical().decode("utf-8") if cal.summary else "(untitled)",
            cal.start.astimezone(tz) + offset,
            cal.end.astimezone(tz),
            type,
            link(cal, tz),
            address(cal),
        )


@dataclasses.dataclass
class CalendarJob:
    event_type: str
    destination: EventDestination


def link(cal: Any, tz: Any) -> str:
    hits = (_LINK.search(str(cal.get(field, ""))) for field in _LINK_FIELDS)
    found = next((hit for hit in hits if hit), None)
    if found:
        return found.group()
    return str(GOOGLE_DAY_URL / f"{cal.start.astimezone(tz):%Y/%m/%d}")


def address(cal: Any) -> str:
    location = str(cal.get("LOCATION", "")).strip()
    if _ADDRESS.match(location):
        return location
    return ""


def is_meeting(url: str) -> bool:
    return bool(_LINK.fullmatch(url))


def opener(url: str) -> str:
    if is_meeting(url):
        return str(URL("/api/push/open").with_query(url=url))
    return url


def schedule_cron(ctx: m.AppContext, backend: FeedService, settings: Any, feeds: list[tuple[str, str]]) -> None:
    # api.rebuild_jobs wipes every jobstore and re-adds only core crons; the
    # listener puts this one back whenever that happens.
    ctx.scheduler.on_rebuild(lambda: _add_cron(ctx, backend, settings, feeds))
    _add_cron(ctx, backend, settings, feeds)


def _add_cron(ctx: m.AppContext, backend: FeedService, settings: Any, feeds: list[tuple[str, str]]) -> None:
    ctx.scheduler.cron(_rebuild, settings.cron, backend, settings, feeds, id=CRON_ID, name="Calendar Cron")


@requires_ctx
def _rebuild(backend: FeedService, settings: Any, feeds: list[tuple[str, str]], *, ctx: m.AppContext) -> None:
    now: datetime = ctx.api.local_now()
    if not ctx.api.is_working_day(now.date()):
        return

    tz = ctx.config.settings.tz
    events_by_id: dict[str, CalendarEvent] = {}
    for name, secret in feeds:
        url = ctx.config.secrets.other[secret]
        events = list(
            islice(backend.fetch_ical(now, timedelta(hours=settings.window_hours), url, settings.http_timeout), settings.max_events)
        )
        warning_events = (CalendarEvent.from_cal(e, name, WARNING, timedelta(minutes=-settings.warning_minutes), tz) for e in events)
        alarm_events = (CalendarEvent.from_cal(e, name, ALARM, timedelta(), tz) for e in events)
        events_by_id.update({e.uuid: e for e in chain(alarm_events, warning_events)})

    for job in ctx.api.fetch_jobs_by_type(CalendarJob):
        if job.id not in events_by_id:
            ctx.scheduler.cancel(job.id)

    for id, event in events_by_id.items():
        ctx.scheduler.once(
            _run_event,
            event.start,
            CalendarJob(event.type, EventDestination(event.summary, event.url, event.address)),
            id=id,
            name=event.summary if event.type == ALARM else f"{event.summary} ({event.type})",
        )


@requires_ctx
def _run_event(job: CalendarJob, *, ctx: m.AppContext) -> None:
    summary = job.destination.summary
    trigger = m.Integration(summary)
    if job.event_type == WARNING:
        entry = m.LogEntry(ctx.api.local_now(), Log.CALENDAR, summary, trigger)
        ctx.api.alert(m.Alarm.ATTENTION, path=ctx.api.DEFAULT_ALERT_PATH, entry=entry)
    else:
        link = str(CHOOSER_URL.with_query(title=summary, url=job.destination.url, address=job.destination.address))
        entry = ctx.api.log(Log.CALENDAR, summary, trigger, notification=m.Notification(("calendar",), link), link=link)
        ctx.api.alert(m.Alarm.ATTENTION, text=summary, entry=entry)
