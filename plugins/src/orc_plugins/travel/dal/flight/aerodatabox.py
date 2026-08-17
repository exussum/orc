from datetime import datetime

import requests

_HOST = "aerodatabox.p.rapidapi.com"


def _time(point: dict) -> datetime:
    t = point.get("revisedTime") or point.get("predictedTime") or point["scheduledTime"]
    return datetime.fromisoformat(t["utc"].replace(" ", "T"))


def arrival(key: str, iata: str, when: datetime, airport: str | None, timeout: int) -> tuple[datetime, str, str | None]:
    response = requests.get(
        f"https://{_HOST}/flights/number/{iata}/{when.date().isoformat()}",
        params={"dateLocalRole": "Both"},
        headers={"x-rapidapi-key": key, "x-rapidapi-host": _HOST},
        timeout=timeout,
    )
    response.raise_for_status()
    flights = [f for f in response.json() if airport is None or f["arrival"]["airport"].get("iata") == airport]
    if not flights:
        raise LookupError(f"no {iata} arrival at {airport or 'any airport'} on {when.date()}")
    a = min(flights, key=lambda f: abs(_time(f["arrival"]) - when))["arrival"]
    return _time(a), a["airport"]["name"], a.get("terminal")
