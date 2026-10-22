from flask import Blueprint, render_template, request

from orc_extras.calendar import plugins

choose = Blueprint("calendar", __name__, template_folder="templates")


@choose.get("/")
def index() -> str:
    choices = zip(request.args.getlist("title"), request.args.getlist("url"), strict=False)
    return render_template("calendar/choose.html", choices=[(title, plugins.opener(url)) for title, url in choices])
