import requests
from yarl import URL

from orc_extras.travel.dal import sqlite

ROUTING = URL("https://api.tomtom.com/routing/1/calculateRoute")
SEARCH = URL("https://api.tomtom.com/search/2/search")


def drive_minutes(key: str, origin: str, dest: str, timeout: int) -> int:
    d = _lookup(key, dest, timeout)
    if d is None:
        raise ValueError(f"could not geocode {dest!r}")
    d_lat, d_lon = d
    response = requests.get(
        str(ROUTING / f"{origin}:{d_lat},{d_lon}" / "json"),
        params={"key": key, "traffic": "true"},
        timeout=timeout,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError:
        if response.status_code == 400:
            sqlite.delete_geocode(dest)
        raise
    return round(response.json()["routes"][0]["summary"]["travelTimeInSeconds"] / 60)


def _lookup(key: str, address: str, timeout: int) -> tuple[float, float] | None:
    if (hit := sqlite.fetch_geocode(address)) is not None:
        return hit
    response = requests.get(
        str(SEARCH / f"{address}.json"),
        params={"key": key, "limit": "1"},
        timeout=timeout,
    )
    response.raise_for_status()
    results = response.json()["results"]
    if not results:
        return None
    pos = results[0]["position"]
    sqlite.insert_geocode(address, pos["lat"], pos["lon"])
    return pos["lat"], pos["lon"]
