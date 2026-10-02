import icalendar
import pytest
from orc_extras.calendar.plugins import link


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
        (_event(description="Agenda: https://docs.example/notes"), ""),
        (_event(description="https://[bad then https://zoom.us/j/5"), "https://zoom.us/j/5"),
        (_event(description="No link here"), ""),
        (_event(), ""),
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
    assert link(event) == expected
