from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import icalendar
import pytest
from flask import Flask
from orc_extras.calendar import plugins, web
from orc_extras.calendar.plugins import ALARM, CalendarJob, link

import orc.view
from orc import model as m

TZ = ZoneInfo("America/New_York")
START = datetime(2026, 1, 6, 1, tzinfo=ZoneInfo("UTC"))
DAY_LINK = "https://calendar.google.com/calendar/r/day/2026/01/05"
ZOOM = "https://zoom.us/j/1"
MEET = "https://meet.google.com/abc-defg-hij"
CHOOSER_LINK = "/api/calendar/choose/?title=A&url=https://zoom.us/j/1&title=B&url=https://meet.google.com/abc-defg-hij"


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


def _running(summary, start_minutes, length_minutes, url):
    start = START + timedelta(minutes=start_minutes)
    event = _event(uid=summary, summary=summary, dtstart=start, location=url)
    if length_minutes:
        event.add("dtend", start + timedelta(minutes=length_minutes))
    return event


@pytest.fixture
def rebuild(ctx):
    ctx.api.local_now.return_value = START
    ctx.api.is_working_day.return_value = True
    ctx.api.fetch_jobs_by_type.return_value = []
    ctx.config.settings.tz = TZ
    ctx.config.secrets.other = {"ICS_URL": "https://feed.example/a.ics"}
    settings = SimpleNamespace(window_hours=20, max_events=50, warning_minutes=2, http_timeout=1)

    def run(events):
        plugins._rebuild(SimpleNamespace(fetch_ical=lambda *args: iter(events)), settings, [("work", "ICS_URL")], ctx=ctx)
        jobs = [call.args[2] for call in ctx.scheduler.once.call_args_list]
        return {job.summary: job.choices for job in jobs if job.event_type == ALARM}

    return run


@pytest.mark.parametrize(
    ("events", "expected"),
    [
        ([_running("A", 0, 30, ZOOM), _running("B", 0, 60, MEET)], {"A": (("A", ZOOM), ("B", MEET)), "B": (("A", ZOOM), ("B", MEET))}),
        ([_running("A", 0, 60, ZOOM), _running("B", 30, 30, MEET)], {"A": (), "B": (("A", ZOOM), ("B", MEET))}),
        ([_running("A", 0, 30, ZOOM), _running("B", 30, 30, MEET)], {"A": (), "B": ()}),
        ([_running("A", 0, 0, ZOOM), _running("B", 0, 0, MEET)], {"A": (("A", ZOOM), ("B", MEET)), "B": (("A", ZOOM), ("B", MEET))}),
        ([_running("A", 0, 30, ZOOM)], {"A": ()}),
    ],
    ids=["same-start", "staggered", "back-to-back", "no-end", "alone"],
)
def test_rebuild_offers_the_running_events_as_choices(rebuild, events, expected):
    assert rebuild(events) == expected


@pytest.mark.parametrize(
    ("choices", "url", "link"),
    [((("A", ZOOM), ("B", MEET)), CHOOSER_LINK, CHOOSER_LINK), ((), ZOOM, f"/api/push/open?url={ZOOM}")],
    ids=["overlapping", "alone"],
)
def test_alarm_links_the_choice_page(ctx, choices, url, link):
    plugins._run_event(CalendarJob(ALARM, "A", ZOOM, choices), ctx=ctx)
    assert ctx.api.log.call_args.kwargs == {"notification": m.Notification(("calendar",), url), "link": link}


@pytest.fixture
def flask_client(ctx):
    app = Flask(__name__, template_folder=str(Path(orc.view.__file__).parent / "templates"))
    app.register_blueprint(orc.view.bp)
    app.register_blueprint(web.choose, url_prefix="/api/calendar/choose")
    app.orc = ctx  # type: ignore[attr-defined]
    return app.test_client()


def test_choose_page_opens_meeting_links_through_push_open(flask_client):
    html = flask_client.get(f"/api/calendar/choose/?title=A&url={ZOOM}&title=B&url={DAY_LINK}").get_data(as_text=True)
    assert 'href="/api/push/open?url=https://zoom.us/j/1">A</a>' in html
    assert f'href="{DAY_LINK}">B</a>' in html
