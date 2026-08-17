import requests
from orc_plugins.travel.dal import sqlite
from orc_plugins.travel.dal.sqlite import Connection


def _geocode(connection: Connection, key: str, address: str, timeout: int) -> tuple[float, float]:
    if (hit := sqlite.fetch_geocode(connection, address)) is not None:
        return hit
    response = requests.get(
        f"https://api.tomtom.com/search/2/search/{requests.utils.quote(address)}.json",
        params={"key": key, "limit": "1"},
        timeout=timeout,
    )
    response.raise_for_status()
    pos = response.json()["results"][0]["position"]
    sqlite.insert_geocode(connection, address, pos["lat"], pos["lon"])
    return pos["lat"], pos["lon"]


def drive_minutes(connection: Connection, key: str, origin: str, dest: str, timeout: int) -> int:
    (o_lat, o_lon), (d_lat, d_lon) = _geocode(connection, key, origin, timeout), _geocode(connection, key, dest, timeout)
    response = requests.get(
        f"https://api.tomtom.com/routing/1/calculateRoute/{o_lat},{o_lon}:{d_lat},{d_lon}/json",
        params={"key": key, "traffic": "true"},
        timeout=timeout,
    )
    response.raise_for_status()
    return round(response.json()["routes"][0]["summary"]["travelTimeInSeconds"] / 60)
