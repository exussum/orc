"""The hub's plain HTTP endpoints: the last Maker API call (reboot) and the
past-log scrape. Everything else (state, events, commands, discovery) moved to
the hub's MQTT export in orc.dal.mqtt."""

import re
from collections import Counter
from datetime import datetime, timedelta
from urllib.parse import urlparse

import requests

import orc
from orc import model as m

_STATS_WINDOW = timedelta(days=2)
_FAILED = "failed after 5 retries"
_RETRIED = "succeeded on retry"
_STATE_CHANGE = re.compile(r"(?:was turned (?:on|off)(?: \[digital\])?|level was set to \d+%)$")


def reboot() -> None:
    resp = requests.post(
        f"{orc.config.settings.hubitat_url}/hub/reboot?access_token={orc.config.secrets.hubitat_access_token}",
        timeout=orc.config.settings.http_timeout,
    )
    resp.raise_for_status()


def fetch_retry_stats() -> tuple[m.RetryStats, ...]:
    base = urlparse(orc.config.settings.hubitat_url or "")
    resp = requests.get(f"{base.scheme}://{base.netloc}/logs/past/json", timeout=orc.config.settings.http_timeout)
    resp.raise_for_status()
    cutoff = datetime.now(orc.config.settings.tz).replace(tzinfo=None) - _STATS_WINDOW
    failed: Counter[int] = Counter()
    retried: Counter[int] = Counter()
    changes: Counter[int] = Counter()
    for line in resp.json():
        fields = line.split("\t")
        parts = fields[-1].split("|", 3)
        if len(parts) != 4 or parts[0] != "dev" or not parts[1].isdigit():
            continue
        elif datetime.fromisoformat(fields[0]) < cutoff:
            continue
        device_id, msg = int(parts[1]), parts[3]
        if _FAILED in msg:
            failed[device_id] += 1
        elif _RETRIED in msg:
            retried[device_id] += 1
        elif _STATE_CHANGE.search(msg):
            changes[device_id] += 1
    return tuple(
        m.RetryStats(id=i, failed=failed[i], clean=max(changes[i] - retried[i], 0), retried=retried[i])
        for i in sorted({*failed, *retried, *changes})
    )
