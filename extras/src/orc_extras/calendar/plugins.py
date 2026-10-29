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
from orc.plugins import requires_ctx
from orc_extras.calendar.dal.interfaces import FeedService

CONFIG = "orc_extras/calendar"
GRAMMAR = """
setting <key> <value>
feed <name> <secret>
"""
WARNING = "warning"
ALARM = "alarm"
CRON_ID = "cal-cron"
CHOOSER_URL = URL("/api/calendar/choose/events")
GOOGLE_DAY_URL = URL("https://calendar.google.com/calendar/r/day")
_LINK_FIELDS = ("X-GOOGLE-CONFERENCE", "X-MICROSOFT-SKYPETEAMSMEETINGURL", "URL", "LOCATION", "DESCRIPTION")
_LINK = re.compile(r"""https?://([\w-]+\.)*(meet\.google\.com|zoom\.us|teams\.microsoft\.com)/[^\s<>"']*""")
_ADDRESS = re.compile(r"^(?!.*://)(?=.*\d)(?=.*,)")
_EMOJI = re.compile(r"[\U0001F000-\U0001FAFF\u2600-\u27BF]\uFE0F?")
_GLYPHS = (
    (re.compile(r"standup|scrum"), "🧍"),
    (re.compile(r"office hours"), "🙋"),
    (re.compile(r"hiring|interview|debrief"), "🎤"),
    (re.compile(r"retro"), "🔁"),
    (re.compile(r"refinement|planning"), "🧩"),
    (re.compile(r"demo"), "🎬"),
    (re.compile(r"all hands"), "📣"),
    (re.compile(r"tech talk|workshop|enablement|training"), "📚"),
    (re.compile(r"reminder"), "⏰"),
    (re.compile(r"holiday"), "🏖️"),
    (re.compile(r"\bdoc\b|busy|hold|focus"), "📝"),
    (re.compile(r"happy hour|drinks"), "🍻"),
    (re.compile(r"lunch|dinner|breakfast"), "🍽️"),
    (re.compile(r"1:1|^\S+ / \S+"), "👥"),
    (re.compile(r"sync|weekly|monthly|leadership"), "🔄"),
)
_LINK_GLYPHS = {"meet.google.com": "🎥", "zoom.us": "💻", "teams.microsoft.com": "👥"}


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


class EventDestination(NamedTuple):
    summary: str
    url: str
    address: str
    glyph: str


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


SERIALIZERS = {
    "setting": scalar(
        Settings, types={"window_hours": cast.int, "max_events": cast.int, "warning_minutes": cast.int, "http_timeout": cast.int}
    ),
    "feed": array(Feed),
}


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


def glyph(summary: str, url: str) -> str:
    if found := _EMOJI.search(summary):
        return found.group()
    title = summary.lower()
    for pattern, symbol in _GLYPHS:
        if pattern.search(title):
            return symbol
    host = URL(url).host or ""
    return next((symbol for domain, symbol in _LINK_GLYPHS.items() if host.endswith(domain)), "📅")


def is_meeting(url: str) -> bool:
    return bool(_LINK.fullmatch(url))


def opener(url: str) -> str:
    if is_meeting(url):
        return str(URL("/api/push/open").with_query(url=url))
    return url


def schedule_cron(ctx: m.AppContext, crontab: str) -> None:
    # api.rebuild_jobs wipes every jobstore and re-adds only core crons; the
    # listener puts this one back whenever that happens.
    ctx.scheduler.on_rebuild(lambda: _add_cron(ctx, crontab))
    _add_cron(ctx, crontab)


@requires_ctx
def schedule_events(*, ctx: m.AppContext) -> None:
    calendar = load_plugin_config(CONFIG, ctx.config, GRAMMAR, SERIALIZERS)
    _schedule(cast.module(calendar.setting.backend), calendar.setting, calendar.feed, ctx=ctx)


def _add_cron(ctx: m.AppContext, crontab: str) -> None:
    ctx.scheduler.cron(schedule_events, crontab, id=CRON_ID, name="Calendar Cron")


def _schedule(backend: FeedService, settings: Settings, feeds: list[Feed], *, ctx: m.AppContext) -> None:
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
            CalendarJob(event.type, EventDestination(event.summary, event.url, event.address, glyph(event.summary, event.url))),
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
