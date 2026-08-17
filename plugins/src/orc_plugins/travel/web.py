from datetime import datetime
from typing import TYPE_CHECKING, cast

from apscheduler.jobstores.base import JobLookupError
from flask import Blueprint, current_app, request
from orc_plugins.travel import plugins
from orc_plugins.travel.model import Submission, TravelJob

if TYPE_CHECKING:
    from orc.view import OrcFlask

travel_bp = Blueprint("travel", __name__)
app = cast("OrcFlask", current_app)


@travel_bp.route("/", methods=["GET"])
def upcoming() -> dict:
    jobs = [j for j in app.orc.scheduler.get_jobs() if j.args and isinstance(j.args[0], TravelJob)]
    jobs.sort(key=lambda j: j.args[0].arrive)
    return {
        "jobs": [
            {"id": j.id, "summary": j.args[0].summary, "arrive": j.args[0].arrive.isoformat(), "airport": j.args[0].airport}
            for j in jobs[:3]
        ],
        "extras": plugins.available_extras(),
        "places": plugins.place_names(),
    }


@travel_bp.route("/", methods=["POST"])
def create() -> tuple[dict, int]:
    ctx = app.orc
    data = request.get_json(silent=True) or {}
    sub = Submission(
        plugins.resolve_place(data.get("destination")),
        datetime.fromisoformat(data["arrive"]) if data.get("arrive") else None,
        data.get("flight"),
        data.get("extras", []),
    )
    try:
        job = TravelJob.from_submission(sub)
    except ValueError as exc:
        return {"error": str(exc)}, 400
    plugins.schedule(ctx.scheduler, job, ctx.config.settings.tz)
    return {"id": job.summary}, 201


@travel_bp.route("/<jid>", methods=["DELETE"])
def delete(jid: str) -> tuple[dict | str, int]:
    try:
        app.orc.scheduler.remove_job(jid)
    except JobLookupError:
        return {"error": "not found"}, 404
    return "", 204
