from itertools import groupby
from typing import TYPE_CHECKING, cast

from flask import Blueprint, current_app, redirect, render_template, request, url_for
from werkzeug.wrappers import Response
from yarl import URL

from orc import model as m
from orc_extras.calendar import plugins
from orc_extras.calendar.plugins import ALARM, CalendarJob, EventDestination

if TYPE_CHECKING:
    from orc.view import OrcFlask

choose = Blueprint("calendar", __name__, template_folder="templates")
app = cast("OrcFlask", current_app)
MAP_URL = URL("https://www.google.com/maps")

Card = tuple[str, str, str, str]


@choose.get("/")
def index() -> str:
    alarms = sorted(app.orc.api.fetch_jobs_by_type(CalendarJob, lambda j: j.event_type == ALARM), key=lambda j: j.trigger.run_date)
    days = [
        (day, [_card(j.args[0].destination) for j in day_jobs])
        for day, day_jobs in groupby(alarms, key=lambda j: j.trigger.run_date.date())
    ]
    return render_template("calendar/choose.html", days=days, refresh=url_for("calendar.refresh"))


@choose.get("/refresh")
def refresh() -> Response:
    plugins.schedule_events(ctx=app.orc)
    app.orc.api.log(plugins.Log.CALENDAR, "Calendar refreshed", m.Manual("calendar"))
    return redirect(url_for("calendar.index"))


@choose.get("/events")
def events() -> str:
    fields = zip(request.args.getlist("title"), request.args.getlist("url"), request.args.getlist("address"), strict=False)
    destinations = [EventDestination(title, url, address, plugins.glyph(title, url)) for title, url, address in fields]
    return render_template("calendar/choose.html", days=[(None, [_card(d) for d in destinations])])


def _card(destination: EventDestination) -> Card:
    return destination.summary, plugins.opener(destination.url), _map(destination.url, destination.address), destination.glyph


def _map(url: str, address: str) -> str:
    if address and not plugins.is_meeting(url):
        return str(MAP_URL.with_query(q=address, output="embed"))
    return ""
