import re
from datetime import timedelta
from typing import NamedTuple

from yarl import URL

MEETING_LINK = re.compile(r"""https?://([\w-]+\.)*(meet\.google\.com|zoom\.us|teams\.microsoft\.com)/[^\s<>"']*""")
_EMOJI = re.compile(r"[\U0001F000-\U0001FAFF☀-➿]️?")
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
RETENTION = timedelta(hours=1).total_seconds()


class EventDestination(NamedTuple):
    summary: str
    url: str
    address: str
    glyph: str


class RecentEvent(NamedTuple):
    title: str
    url: str
    address: str
    glyph: str
    fired: float


_events: list[RecentEvent] = []


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
    return bool(MEETING_LINK.fullmatch(url))


def opener(url: str) -> str:
    if is_meeting(url):
        return str(URL("/api/push/open").with_query(url=url))
    return url


def record(event: RecentEvent) -> None:
    _events[:] = [e for e in _events if e.fired >= event.fired - RETENTION]
    _events.append(event)


def fetch_recent(now: float) -> list[RecentEvent]:
    return sorted((e for e in _events if e.fired >= now - RETENTION), key=lambda e: e.fired, reverse=True)
