from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import icalendar
import pytest
from flask import Flask
from orc_extras.calendar import plugins, web
from orc_extras.calendar.plugins import ALARM, CalendarJob, EventDestination, address, link

import orc.view
from orc import model as m

TZ = ZoneInfo("America/New_York")
START = datetime(2026, 1, 6, 1, tzinfo=ZoneInfo("UTC"))
DAY_LINK = "https://calendar.google.com/calendar/r/day/2026/01/05"
ZOOM = "https://zoom.us/j/1"
MEET = "https://meet.google.com/abc-defg-hij"
A = EventDestination("A", ZOOM, "", "💻")
B = EventDestination("B", MEET, "", "🎥")
ADDRESS = "1 Main St, Springfield, IL 62701"
CHOOSER_LINK = "/api/calendar/choose/events?title=A&url=https://zoom.us/j/1&address="


def _event(**fields):
    event = icalendar.Event()
    for name, value in fields.items():
        event.add(name.replace("_", "-"), value)
    return event


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (
            _event(x_google_conference="https://meet.google.com/abc-defg-hij", description="Join: https://zoom.us/j/1"),
            "https://meet.google.com/abc-defg-hij",
        ),
        (_event(location="https://zoom.us/j/123?pwd=x", description="Dial-in"), "https://zoom.us/j/123?pwd=x"),
        (_event(location="Room 4", description='Join <a href="https://zoom.us/j/9">here</a>'), "https://zoom.us/j/9"),
        (
            _event(
                location="Microsoft Teams Meeting",
                description="Need help? <https://aka.ms/JoinTeamsMeeting> Join now <https://teams.microsoft.com/l/meetup-join/19%3a1>",
            ),
            "https://teams.microsoft.com/l/meetup-join/19%3a1",
        ),
        (
            _event(x_microsoft_skypeteamsmeetingurl="https://teams.microsoft.com/l/meetup-join/2", description="https://aka.ms/x"),
            "https://teams.microsoft.com/l/meetup-join/2",
        ),
        (_event(dtstart=START, description="Agenda: https://docs.example/notes"), DAY_LINK),
        (_event(description="https://[bad then https://zoom.us/j/5"), "https://zoom.us/j/5"),
        (_event(dtstart=START, description="No link here"), DAY_LINK),
        (_event(dtstart=START), DAY_LINK),
    ],
    ids=[
        "meet-first",
        "location",
        "description-html",
        "teams-description",
        "teams-field",
        "other-link",
        "malformed-first",
        "no-link",
        "empty",
    ],
)
def test_link_prefers_a_meeting_host(event, expected):
    assert link(event, TZ) == expected


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (_event(location=ADDRESS), ADDRESS),
        (_event(location="Room 4"), ""),
        (_event(location="Microsoft Teams Meeting"), ""),
        (_event(location=ZOOM), ""),
        (_event(), ""),
    ],
    ids=["street", "room", "teams", "link", "none"],
)
def test_address_needs_a_number_and_a_comma(event, expected):
    assert address(event) == expected


def _timed_event(summary, start_minutes, length_minutes, url):
    start = START + timedelta(minutes=start_minutes)
    event = _event(uid=summary, summary=summary, dtstart=start, location=url)
    if length_minutes:
        event.add("dtend", start + timedelta(minutes=length_minutes))
    return event


def test_floating_event_takes_the_window_timezone():
    from unittest.mock import patch

    from orc_extras.calendar.dal import ical

    ics = b"BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:a\nSUMMARY:Floating\nDTSTART:20260106T090000\nDURATION:PT30M\nEND:VEVENT\nEND:VCALENDAR\n"
    start = datetime(2026, 1, 6, 1, tzinfo=TZ)
    with patch.object(ical.requests, "get", return_value=SimpleNamespace(content=ics)):
        (event,) = ical.fetch_ical(start, timedelta(hours=20), "https://feed.example/a.ics", 1)
    assert (event.start, event.end) == (datetime(2026, 1, 6, 9, tzinfo=TZ), datetime(2026, 1, 6, 9, 30, tzinfo=TZ))


def test_each_occurrence_gets_its_own_jobs(rebuild, ctx):
    rebuild([_timed_event("A", 0, 30, ZOOM), _timed_event("A", 720, 30, ZOOM)])
    ids = {call.kwargs["id"] for call in ctx.scheduler.once.call_args_list}
    assert len(ids) == 4


def test_event_without_summary_is_untitled(rebuild):
    assert rebuild([_event(uid="A", dtstart=START, dtend=START + timedelta(minutes=30))]) == {
        "(untitled)": EventDestination("(untitled)", DAY_LINK, "", "📅")
    }


@pytest.fixture
def rebuild(ctx):
    ctx.api.local_now.return_value = START
    ctx.api.is_working_day.return_value = True
    ctx.api.fetch_jobs_by_type.return_value = []
    ctx.config.settings.tz = TZ
    ctx.config.secrets.other = {"ICS_URL": "https://feed.example/a.ics"}
    settings = SimpleNamespace(window_hours=20, max_events=50, warning_minutes=2, http_timeout=1)

    def run(events):
        plugins._schedule(SimpleNamespace(fetch_ical=lambda *args: iter(events)), settings, [("work", "ICS_URL")], ctx=ctx)
        jobs = [call.args[2] for call in ctx.scheduler.once.call_args_list]
        return {job.destination.summary: job.destination for job in jobs if job.event_type == ALARM}

    return run


def test_alarm_links_the_choice_page(ctx):
    plugins._run_event(CalendarJob(ALARM, A), ctx=ctx)
    assert ctx.api.log.call_args.kwargs == {"notification": m.Notification(("calendar",), CHOOSER_LINK), "link": CHOOSER_LINK}


@pytest.fixture
def flask_client(ctx):
    app = Flask(__name__, template_folder=str(Path(orc.view.__file__).parent / "templates"))
    app.register_blueprint(orc.view.bp)
    app.register_blueprint(web.choose, url_prefix="/api/calendar/choose")
    app.orc = ctx  # type: ignore[attr-defined]
    return app.test_client()


def test_choose_page_opens_meeting_links_through_push_open(flask_client):
    html = flask_client.get(f"/api/calendar/choose/events?title=A&url={ZOOM}&address=&title=B&url={DAY_LINK}&address=").get_data(
        as_text=True
    )
    assert 'href="/api/push/open?url=https://zoom.us/j/1" title="A">' in html
    assert f'href="{DAY_LINK}" title="B">' in html
    assert "<iframe" not in html


def test_choose_page_groups_scheduled_alarms_by_day(flask_client, ctx):
    def job(kind, destination, hours):
        return SimpleNamespace(args=(CalendarJob(kind, destination),), trigger=SimpleNamespace(run_date=START + timedelta(hours=hours)))

    ctx.api.fetch_jobs_by_type.return_value = [job(ALARM, B, 30), job(ALARM, A, 1)]
    html = flask_client.get("/api/calendar/choose/").get_data(as_text=True)
    assert html.index("Tuesday, January 6") < html.index(">A</span></a>") < html.index("Wednesday, January 7") < html.index(">B</span></a>")
    assert html.count('<span class="truncate">') == 2


def test_choose_page_says_when_nothing_is_scheduled(flask_client, ctx):
    ctx.api.fetch_jobs_by_type.return_value = []
    assert "No calendar events." in flask_client.get("/api/calendar/choose/").get_data(as_text=True)


def test_choose_page_maps_an_address(flask_client):
    html = flask_client.get(f"/api/calendar/choose/events?title=A&url={DAY_LINK}&address={ADDRESS}").get_data(as_text=True)
    assert 'src="https://www.google.com/maps?q=1+Main+St,+Springfield,+IL+62701&amp;output=embed"' in html


def test_choose_page_skips_the_map_for_a_meeting_link(flask_client):
    html = flask_client.get(f"/api/calendar/choose/events?title=A&url={ZOOM}&address={ADDRESS}").get_data(as_text=True)
    assert "<iframe" not in html
