import os
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import date, datetime
from functools import lru_cache, wraps
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from urllib.request import urlopen

import icalendar
import icmplib
import pychromecast
import recurring_ical_events
import requests
import yt_dlp
from bitwarden_sdk import BitwardenClient, DeviceType, client_settings_from_dict
from sqlalchemy.engine.url import make_url

from orc import config
from orc import model as m

_YDL_OPTS = {
    "format": "bestaudio/best",  # Request the highest quality audio stream
    "quiet": True,
    "no_warnings": True,
}

_DB_TRUTH_DEVICE_TYPES = {"Generic Zigbee Outlet"}


def requires_enabled(stub):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not os.getenv("ORC_ENABLED"):
                print(f"[disabled] {fn.__name__} args={args} kwargs={kwargs}", file=sys.stderr)
                return stub(*args, **kwargs) if callable(stub) else stub
            return fn(*args, **kwargs)

        return wrapper

    return deco


# --- Database ---


def delete_presence(name):
    with _theme_override_conn() as conn:
        conn.execute("DELETE FROM orc_presence WHERE name = ?", (name,))


def delete_theme_override():
    with _theme_override_conn() as conn:
        conn.execute("DELETE FROM orc_theme_override WHERE id = 0")


def fetch_presence():
    with _theme_override_conn() as conn:
        rows = conn.execute("SELECT name, last_seen FROM orc_presence").fetchall()
    return {name: datetime.fromisoformat(last_seen) for name, last_seen in rows}


def fetch_theme_override():
    with _theme_override_conn() as conn:
        row = conn.execute("SELECT name, start, end FROM orc_theme_override WHERE id = 0").fetchone()
    if not row:
        return None
    return (row[0], date.fromisoformat(row[1]), date.fromisoformat(row[2]))


def init_db():
    with _theme_override_conn() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS orc_theme_override "
            "(id INTEGER PRIMARY KEY CHECK (id = 0), name TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL)"
        )
        conn.execute("CREATE TABLE IF NOT EXISTS orc_presence (name TEXT PRIMARY KEY, last_seen TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS orc_light (device_id INTEGER PRIMARY KEY, type TEXT, state TEXT)")


def insert_presence(name, when):
    with _theme_override_conn() as conn:
        conn.execute(
            "INSERT INTO orc_presence (name, last_seen) VALUES (?, ?) " "ON CONFLICT(name) DO UPDATE SET last_seen=excluded.last_seen",
            (name, when.isoformat()),
        )


def insert_theme_override(override):
    with _theme_override_conn() as conn:
        conn.execute(
            "INSERT INTO orc_theme_override (id, name, start, end) VALUES (0, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, start=excluded.start, end=excluded.end",
            (override[0], override[1].isoformat(), override[2].isoformat()),
        )


def purge_presence():
    with _theme_override_conn() as conn:
        conn.execute("DELETE FROM orc_presence")


def _fetch_hubitat_device(light):
    resp = requests.get(f"{config.base_url}/devices/{light.value}{config.secrets.access_token}", timeout=config.http_timeout)
    return resp.json() if resp.status_code == 200 else None


def _read_light(light):
    with _theme_override_conn() as conn:
        row = conn.execute("SELECT type, state FROM orc_light WHERE device_id = ?", (light.value,)).fetchone()
    return row if row else (None, None)


def _theme_override_conn():
    return sqlite3.connect(make_url(config.jobs_db).database)


def _write_light(light, *, type=None, state=None):
    with _theme_override_conn() as conn:
        conn.execute(
            "INSERT INTO orc_light (device_id, type, state) VALUES (?, ?, ?) "
            "ON CONFLICT(device_id) DO UPDATE SET "
            "type = COALESCE(excluded.type, orc_light.type), "
            "state = COALESCE(excluded.state, orc_light.state)",
            (light.value, type, str(state) if state is not None else None),
        )


# --- Secrets ---


def fetch_secrets():
    c = BitwardenClient(
        client_settings_from_dict(
            {
                "apiUrl": "https://vault.bitwarden.com/api",
                "identityUrl": "https://vault.bitwarden.com/identity",
                "userAgent": "orc",
                "deviceType": DeviceType.SDK,
            }
        )
    )
    c.auth().login_access_token(_get_url_value(os.environ["BWS_ACCESS_TOKEN"]))
    secrets = c.secrets().list(_get_url_value(os.environ["BWS_ORG_ID"])).data

    def get_secret(secret_name):
        return next(c.secrets().get(e.id).data.value for e in secrets.data if e.key == secret_name)

    return m.Secrets(
        access_token="?access_token=" + get_secret("HUBITAT_ACCESS_TOKEN"),
        market_holidays_url=get_secret("MARKET_HOLIDAYS_URL"),
        ics_url=get_secret("ICS_URL"),
    )


def _get_url_value(url):
    with urlopen(url) as response:
        return response.readline().decode("utf-8").strip()


# --- Lights ---


@requires_enabled(lambda light: m.Config(what=light, state=config.OFF))
def fetch_light_state(light):
    device_type, stored = _read_light(light)
    body = _fetch_hubitat_device(light)

    if device_type in _DB_TRUTH_DEVICE_TYPES:
        return m.Config(what=light, state=stored or config.OFF)

    if body is None:
        return m.Config(what=light, state=config.OFF)

    if device_type is None:
        device_type = body.get("type", "")
        _write_light(light, type=device_type)
        if device_type in _DB_TRUTH_DEVICE_TYPES:
            return m.Config(what=light, state=stored or config.OFF)

    attrs = {e["name"]: e["currentValue"] for e in body["attributes"]}
    return m.Config(what=light, state=attrs["level"] if ("level" in attrs and attrs["switch"] == config.ON) else attrs["switch"])


@requires_enabled(None)
def update_light(light, on=None, brightness=None):
    if brightness is not None:
        requests.get(
            f"{config.base_url}/devices/{light.value}/setLevel/{brightness}{config.secrets.access_token}",
            timeout=config.http_timeout,
        )
        new_state = brightness
    else:
        requests.get(
            f"{config.base_url}/devices/{light.value}/{config.ON if on else config.OFF}{config.secrets.access_token}",
            timeout=config.http_timeout,
        )
        new_state = config.ON if on else config.OFF
    device_type, _ = _read_light(light)
    type_to_store = None
    if device_type is None:
        body = _fetch_hubitat_device(light)
        if body is not None:
            device_type = body.get("type", "")
            type_to_store = device_type

    state_to_store = new_state if device_type in _DB_TRUTH_DEVICE_TYPES else None
    if type_to_store is not None or state_to_store is not None:
        _write_light(light, type=type_to_store, state=state_to_store)


# --- Sound ---


@requires_enabled(lambda sound: m.SoundState(what=sound, content=None, volume=0))
def fetch_sound(sound):
    with _cast(sound, timeout=5, tries=1) as cast:
        time.sleep(3)
        content = cast.media_controller.status.content_id
        return m.SoundState(
            what=sound,
            content=_strip_googlevideo_params(content) if content else None,
            volume=int(cast.status.volume_level * 100),
        )


@requires_enabled(lambda *_: ("", "Audio Stream"))
def fetch_youtube(id):
    with yt_dlp.YoutubeDL(_YDL_OPTS) as ydl:
        info = ydl.extract_info(id, download=False)
        return info["url"], info.get("title", "Audio Stream")


@requires_enabled(None)
def pause_sound(sound):
    with _cast(sound) as cast:
        cast.media_controller.update_status()
        time.sleep(1)
        if cast.media_controller.status.player_state in ("PLAYING", "BUFFERING"):
            cast.media_controller.pause()
            time.sleep(1)


@requires_enabled(None)
def play_stream(sound, stream_url, title):
    with _cast(sound) as cast:
        cast.media_controller.play_media(stream_url, "audio/mp3", title=title)
        cast.media_controller.block_until_active(timeout=10)


@requires_enabled(None)
def resume_sound(sound):
    with _cast(sound) as cast:
        cast.media_controller.update_status()
        time.sleep(1)
        if cast.media_controller.status.player_state == "PAUSED":
            cast.media_controller.play()
            time.sleep(1)


@requires_enabled(None)
def stop_sound(sound):
    with _cast(sound) as cast:
        cast.quit_app()
        time.sleep(1)


@requires_enabled(None)
def update_sound(sound, lvl):
    with _cast(sound) as cast:
        cast.set_volume(lvl / 100)
        time.sleep(1)


@contextmanager
def _cast(sound, **kwargs):
    cast = pychromecast.get_chromecast_from_host((sound.value, 8009, None, None, None), **kwargs)
    try:
        cast.wait(timeout=kwargs.get("timeout"))
        yield cast
    finally:
        cast.disconnect(timeout=2)


def _strip_googlevideo_params(url):
    parsed = urlparse(url)
    if not parsed.hostname or not parsed.hostname.endswith("googlevideo.com"):
        return url
    vid_id = parse_qs(parsed.query).get("id", [None])[0]
    query = urlencode({"id": vid_id}) if vid_id is not None else ""
    return urlunparse(parsed._replace(query=query))


# --- Discovery ---


@requires_enabled({})
def fetch_chromecast_config():
    chromecasts, browser = pychromecast.get_chromecasts()
    devices = {e.cast_info.friendly_name: e.cast_info.host for e in chromecasts}
    pychromecast.discovery.stop_discovery(browser)
    return devices


@requires_enabled({})
def fetch_hubitat_config(secrets):
    result = requests.get(f"{config.base_url}/devices{secrets.access_token}", timeout=config.http_timeout).json()
    return {e["label"]: int(e["id"]) for e in result}


@requires_enabled(False)
def ping_host(hostname):
    return icmplib.ping(hostname, count=2, timeout=1, privileged=True).is_alive


# --- External feeds ---


@requires_enabled([])
@lru_cache(maxsize=2)
def fetch_holidays(year):
    result = requests.get(config.secrets.market_holidays_url, timeout=config.http_timeout).json()
    if "error" in result:
        print(result["error"], file=sys.stderr)
        return []
    return result


@requires_enabled(lambda *_: iter(()))
def fetch_ical(start, end):
    ical_string = requests.get(config.secrets.ics_url, timeout=config.http_ical_timeout).content
    a_calendar = icalendar.Calendar.from_ical(ical_string)
    return (e for e in recurring_ical_events.of(a_calendar).between(start, end) if type(e.start) is datetime and e.start >= start)
