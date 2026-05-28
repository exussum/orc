import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from functools import lru_cache
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


def _theme_override_conn():
    return sqlite3.connect(make_url(config.jobs_db).database)


def init_db():
    with _theme_override_conn() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS orc_theme_override "
            "(id INTEGER PRIMARY KEY CHECK (id = 0), name TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS orc_presence (name TEXT PRIMARY KEY, last_seen TEXT NOT NULL)"
        )


def load_theme_override():
    with _theme_override_conn() as conn:
        row = conn.execute("SELECT name, start, end FROM orc_theme_override WHERE id = 0").fetchone()
    if not row:
        return None
    return (row[0], date.fromisoformat(row[1]), date.fromisoformat(row[2]))


def save_theme_override(override):
    with _theme_override_conn() as conn:
        if override is None:
            conn.execute("DELETE FROM orc_theme_override WHERE id = 0")
        else:
            conn.execute(
                "INSERT INTO orc_theme_override (id, name, start, end) VALUES (0, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, start=excluded.start, end=excluded.end",
                (override[0], override[1].isoformat(), override[2].isoformat()),
            )


def load_presence():
    with _theme_override_conn() as conn:
        rows = conn.execute("SELECT name, last_seen FROM orc_presence").fetchall()
    return {name: datetime.fromisoformat(last_seen) for name, last_seen in rows}


def save_presence(name, when):
    with _theme_override_conn() as conn:
        conn.execute(
            "INSERT INTO orc_presence (name, last_seen) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET last_seen=excluded.last_seen",
            (name, when.isoformat()),
        )


def delete_presence(name):
    with _theme_override_conn() as conn:
        conn.execute("DELETE FROM orc_presence WHERE name = ?", (name,))


def ping_host(hostname, timeout=2):
    return icmplib.ping(hostname, count=3, timeout=timeout, privileged=True).is_alive


_YDL_OPTS = {
    "format": "bestaudio/best",  # Request the highest quality audio stream
    "quiet": True,
    "no_warnings": True,
}


def _get_url_value(url):
    with urlopen(url) as response:
        return response.readline().decode("utf-8").strip()


def get_secrets():
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


def get_light_state(light):
    resp = requests.get(f"{config.base_url}/devices/{light.value}{config.secrets.access_token}", timeout=config.http_timeout)
    if resp.status_code == 200:
        attrs = {e["name"]: e["currentValue"] for e in resp.json()["attributes"]}
        return m.Config(what=light, state=attrs["level"] if ("level" in attrs and attrs["switch"] == "on") else attrs["switch"])
    else:
        return m.Config(what=light, state="off")


def _strip_googlevideo_params(url):
    parsed = urlparse(url)
    if not parsed.hostname or not parsed.hostname.endswith("googlevideo.com"):
        return url
    vid_id = parse_qs(parsed.query).get("id", [None])[0]
    query = urlencode({"id": vid_id}) if vid_id is not None else ""
    return urlunparse(parsed._replace(query=query))


def _fetch_sound(sound):
    cast = pychromecast.get_chromecast_from_host((sound.value, 8009, None, None, None), timeout=5, tries=1)
    cast.wait(timeout=5)
    rh = pychromecast.response_handler.WaitResponse(5.0, "media status")
    cast.media_controller.update_status(callback_function=rh.callback)
    rh.wait_response()
    content = cast.media_controller.status.content_id
    return m.SoundState(
        what=sound,
        content=_strip_googlevideo_params(content) if content else None,
        volume=int(cast.status.volume_level * 100),
    )


def get_sound(Sound):
    with ThreadPoolExecutor(max_workers=len(Sound)) as ex:
        return tuple(ex.map(_fetch_sound, Sound))


def set_light(light, on=None, brightness=None):
    if brightness is not None:
        requests.get(
            f"{config.base_url}/devices/{light.value}/setLevel/{brightness}{config.secrets.access_token}",
            timeout=config.http_timeout,
        )
    else:
        requests.get(
            f"{config.base_url}/devices/{light.value}/{'on' if on else 'off'}{config.secrets.access_token}",
            timeout=config.http_timeout,
        )


def set_sound(sound, lvl):
    cast = pychromecast.get_chromecast_from_host((sound.value, 8009, None, None, None))
    cast.wait()
    cast.set_volume(lvl / 100)
    time.sleep(1)


def stop_sound(sound):
    cast = pychromecast.get_chromecast_from_host((sound.value, 8009, None, None, None))
    cast.wait()
    cast.quit_app()
    time.sleep(1)


def resolve_youtube(id):
    with yt_dlp.YoutubeDL(_YDL_OPTS) as ydl:
        info = ydl.extract_info(id, download=False)
        return info["url"], info.get("title", "Audio Stream")


def play_stream(sound, stream_url, title):
    cast = pychromecast.get_chromecast_from_host((sound.value, 8009, None, None, None))
    cast.wait()
    cast.quit_app()
    cast.media_controller.play_media(stream_url, "audio/mp3", title=title)
    time.sleep(1)


def get_hubitat_config(secrets):
    result = requests.get(f"{config.base_url}/devices{secrets.access_token}", timeout=config.http_timeout).json()
    return {e["label"]: int(e["id"]) for e in result}


def get_chromecast_config():
    chromecasts, browser = pychromecast.get_chromecasts()
    devices = {e.cast_info.friendly_name: e.cast_info.host for e in chromecasts}
    pychromecast.discovery.stop_discovery(browser)
    return devices


@lru_cache(maxsize=2)
def get_holidays(year):
    if not config.enabled:
        return []
    result = requests.get(config.secrets.market_holidays_url, timeout=config.http_timeout).json()
    if "error" in result:
        print(result["error"])
        return []
    return result


def read_ical(start, end):
    ical_string = requests.get(config.secrets.ics_url, timeout=config.http_ical_timeout).content
    a_calendar = icalendar.Calendar.from_ical(ical_string)
    return (e for e in recurring_ical_events.of(a_calendar).between(start, end) if type(e.start) is datetime and e.start >= start)
