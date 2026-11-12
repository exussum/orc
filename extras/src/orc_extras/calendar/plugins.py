import dataclasses
import re
from datetime import datetime, timedelta
from itertools import chain, islice
from typing import Any, NamedTuple

from command_cfg import array, scalar
from orc_engine import cast
from yarl import URL

from orc import model as m
from orc.kernel.loader import load_plugin_config
from orc_extras.calendar.api import MEETING_LINK, EventDestination, RecentEvent, glyph, record
from orc_extras.calendar.dal.interfaces import FeedService

CONFIG = "orc_extras/calendar"
GRAMMAR = """
setting <key> <value>
feed <name> <secret> [<theme>]
"""
WARNING = "warning"
ALARM = "alarm"
CRON_ID = "cal-cron"
REFRESH_ID = "cal-refresh"
QUEUE = m.QueueName("calendar")
CHOOSER_URL = URL("/api/calendar/choose/events")
GOOGLE_DAY_URL = URL("https://calendar.google.com/calendar/r/day")
_LINK_FIELDS = ("X-GOOGLE-CONFERENCE", "X-MICROSOFT-SKYPETEAMSMEETINGURL", "URL", "LOCATION", "DESCRIPTION")
_ADDRESS = re.compile(r"^(?!.*://)(?=.*\d)(?=.*,)")


class Log(m.LogSourceEnum):
    CALENDAR = "calendar"


class Settings(NamedTuple):
    backend: str
    cron: str
    window_hours: int
    max_events: int
    warning_minutes: int
    http_timeout: int


class Feed(NamedTuple):
    name: str
    secret: str
    theme: str | None = None


@dataclasses.dataclass
class CalendarEvent:
    uuid: str
    summary: str
    start: datetime
    end: datetime
    type: str
    url: str
    address: str
    theme: str | None

    @staticmethod
    def from_cal(cal: Any, feed: str, type: str, offset: timedelta, tz: Any, theme: str | None) -> CalendarEvent:
        return CalendarEvent(
            feed + " " + cal.uid.to_ical().decode() + " " + cal.start.astimezone(tz).isoformat() + " " + type,
            cal.summary.to_ical().decode("utf-8") if cal.summary else "(untitled)",
            cal.start.astimezone(tz) + offset,
            cal.end.astimezone(tz),
            type,
            link(cal, tz),
            address(cal),
            theme,
        )


@dataclasses.dataclass
class CalendarJob:
    event_type: str
    destination: EventDestination
    theme: str | None = None


SERIALIZERS = {
    "setting": scalar(
        Settings, types={"window_hours": cast.int, "max_events": cast.int, "warning_minutes": cast.int, "http_timeout": cast.int}
    ),
    "feed": array(Feed),
}


def link(cal: Any, tz: Any) -> str:
    hits = (MEETING_LINK.search(str(cal.get(field, ""))) for field in _LINK_FIELDS)
    found = next((hit for hit in hits if hit), None)
    if found:
        return found.group()
    return str(GOOGLE_DAY_URL / f"{cal.start.astimezone(tz):%Y/%m/%d}")


def address(cal: Any) -> str:
    location = str(cal.get("LOCATION", "")).strip()
    if _ADDRESS.match(location):
        return location
    return ""


def schedule_cron(ctx: m.AppContext, crontab: str) -> None:
    ctx.scheduler.cron(schedule_events, crontab, id=CRON_ID, name="Calendar Cron", queue=m.Queue.FETCH)


def schedule_events(*, ctx: m.AppContext) -> None:
    calendar = load_plugin_config(CONFIG, ctx.config, GRAMMAR, SERIALIZERS)
    _schedule(cast.module(calendar.setting.backend), calendar.setting, calendar.feed, ctx=ctx)


def _schedule(backend: FeedService, settings: Settings, feeds: list[Feed], *, ctx: m.AppContext) -> None:
    now: datetime = ctx.api.local_now()
    tz = ctx.config.settings.tz
    events_by_id: dict[str, CalendarEvent] = {}
    for name, secret, theme in feeds:
        url = ctx.config.secrets.other[secret]
        events = list(
            islice(backend.fetch_ical(now, timedelta(hours=settings.window_hours), url, settings.http_timeout), settings.max_events)
        )
        warning_events = (CalendarEvent.from_cal(e, name, WARNING, timedelta(minutes=-settings.warning_minutes), tz, theme) for e in events)
        alarm_events = (CalendarEvent.from_cal(e, name, ALARM, timedelta(), tz, theme) for e in events)
        events_by_id.update({e.uuid: e for e in chain(alarm_events, warning_events)})

    for job in ctx.api.fetch_jobs(QUEUE):
        if job.id not in events_by_id:
            ctx.scheduler.cancel(job.id, QUEUE)

    for id, event in events_by_id.items():
        ctx.scheduler.once(
            _run_event,
            event.start,
            CalendarJob(
                event.type,
                EventDestination(event.summary, event.url, event.address, glyph(event.summary, event.url)),
                event.theme,
            ),
            id=id,
            name=event.summary if event.type == ALARM else f"{event.summary} ({event.type})",
            queue=QUEUE,
        )


def _run_event(payload: CalendarJob, *, ctx: m.AppContext) -> None:
    if payload.theme and payload.theme != ctx.api.calculate_theme(ctx.api.local_now().date()):
        return
    summary = payload.destination.summary
    trigger = m.Integration(summary)
    if payload.event_type == WARNING:
        entry = m.LogEntry(ctx.api.local_now(), Log.CALENDAR, summary, trigger)
        ctx.api.alert(m.Alarm.ATTENTION, path=ctx.api.DEFAULT_ALERT_PATH, entry=entry)
    else:
        destination = payload.destination
        record(RecentEvent(*destination, ctx.api.local_now().timestamp()))
        link = str(CHOOSER_URL.with_query(title=summary, url=destination.url, address=destination.address))
        entry = ctx.log.write(Log.CALENDAR, summary, trigger, notification=m.Notification(("calendar",), link), link=link)
        ctx.api.alert(m.Alarm.ATTENTION, text=summary, entry=entry)
