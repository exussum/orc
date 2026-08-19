import requests
from orc_plugins.travel.dal import sqlite
from orc_plugins.travel.dal.sqlite import Connection


def _lookup(connection: Connection, key: str, address: str, timeout: int) -> tuple[float, float] | None:
    if (hit := sqlite.fetch_geocode(connection, address)) is not None:
        return hit
    response = requests.get(
        f"https://api.tomtom.com/search/2/search/{requests.utils.quote(address)}.json",
        params={"key": key, "limit": "1"},
        timeout=timeout,
    )
    response.raise_for_status()
    results = response.json()["results"]
    if not results:
        return None
    pos = results[0]["position"]
    sqlite.insert_geocode(connection, address, pos["lat"], pos["lon"])
    return pos["lat"], pos["lon"]


def geocode(connection: Connection, key: str, address: str, timeout: int) -> bool:
    return _lookup(connection, key, address, timeout) is not None


def drive_minutes(connection: Connection, key: str, origin: str, dest: str, timeout: int) -> int:
    d = _lookup(connection, key, dest, timeout)
    if d is None:
        raise ValueError(f"could not geocode {dest!r}")
    d_lat, d_lon = d
    response = requests.get(
        f"https://api.tomtom.com/routing/1/calculateRoute/{origin}:{d_lat},{d_lon}/json",
        params={"key": key, "traffic": "true"},
        timeout=timeout,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError:
        sqlite.delete_geocode(connection, dest)
        raise
    return round(response.json()["routes"][0]["summary"]["travelTimeInSeconds"] / 60)
