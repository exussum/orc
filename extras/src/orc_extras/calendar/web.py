from flask import Blueprint, render_template, request
from yarl import URL

from orc_extras.calendar import plugins

choose = Blueprint("calendar", __name__, template_folder="templates")
MAP_URL = URL("https://www.google.com/maps")


@choose.get("/")
def index() -> str:
    choices = zip(request.args.getlist("title"), request.args.getlist("url"), request.args.getlist("address"), strict=False)
    return render_template(
        "calendar/choose.html",
        choices=[(title, plugins.opener(url), _map(address)) for title, url, address in choices],
    )


def _map(address: str) -> str:
    if address:
        return str(MAP_URL.with_query(q=address, output="embed"))
    return ""
