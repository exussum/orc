from datetime import datetime
from typing import TYPE_CHECKING, cast

from apscheduler.jobstores.base import JobLookupError
from flask import Blueprint, current_app, request

from orc_extras.travel import plugins
from orc_extras.travel.model import Submission, TravelJob

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
            {
                "id": j.id,
                "summary": j.args[0].summary,
                "arrive": j.args[0].arrive.isoformat(),
                "airport": j.args[0].airport,
                "leave_at": j.args[0].leave_at.isoformat() if j.args[0].leave_at else None,
                "late": j.args[0].late,
                "eta": j.args[0].eta.isoformat() if j.args[0].eta else None,
            }
            for j in jobs[:3]
        ],
        "extras": [{"name": e.name, "minutes": e.minutes} for e in plugins.available_extras()],
        "places": plugins.place_names(),
    }


@travel_bp.route("/", methods=["POST"])
def create() -> tuple[dict, int]:
    ctx = app.orc
    data = request.get_json(silent=True) or {}
    target = (data.get("target") or "").strip()
    if plugins.is_flight(target):
        flight, destination = target.replace(" ", "").upper(), None
    else:
        flight, destination = None, (target or None)
    sub = Submission(
        plugins.resolve_place(destination),
        datetime.fromisoformat(data["arrive"]) if data.get("arrive") else None,
        flight,
        data.get("extras", []),
        destination,
    )
    try:
        job = TravelJob.from_submission(sub)
        arrival, sched = plugins.evaluate(job, ctx.config.settings.tz, ctx.api.local_now(), ctx.api.connection)
        if arrival is not None:
            job.arrive = arrival.when
        job.leave_at, job.late, job.eta = sched.leave_at, sched.late, sched.eta
    except ValueError as exc:
        return {"error": str(exc)}, 400
    plugins.schedule(ctx.scheduler, job, ctx.config.settings.tz)
    return {
        "id": job.summary,
        "leave_at": job.leave_at.isoformat() if job.leave_at else None,
        "late": job.late,
        "eta": job.eta.isoformat() if job.eta else None,
    }, 201


@travel_bp.route("/<jid>", methods=["DELETE"])
def delete(jid: str) -> tuple[dict | str, int]:
    try:
        app.orc.scheduler.remove_job(jid)
    except JobLookupError:
        return {"error": "not found"}, 404
    return "", 204
