"""The hub's plain HTTP endpoints: the last Maker API call (reboot) and the
past-log scrape. Everything else (state, events, commands, discovery) moved to
the hub's MQTT export in orc.dal.mqtt."""

import re
from collections import Counter
from urllib.parse import urlparse

import requests

from orc import config
from orc import model as m

_FAILED = "failed after 5 retries"
_RETRIED = "succeeded on retry"
_STATE_CHANGE = re.compile(r"(?:was turned (?:on|off)(?: \[digital\])?|level was set to \d+%)$")


def reboot() -> None:
    resp = requests.post(f"{config.hubitat_url}/hub/reboot?access_token={config.secrets.hubitat_access_token}", timeout=config.http_timeout)
    resp.raise_for_status()


def fetch_retry_stats() -> tuple[m.RetryStats, ...]:
    base = urlparse(config.hubitat_url or "")
    resp = requests.get(f"{base.scheme}://{base.netloc}/logs/past/json", timeout=config.http_timeout)
    resp.raise_for_status()
    failed: Counter[int] = Counter()
    retried: Counter[int] = Counter()
    changes: Counter[int] = Counter()
    for line in resp.json():
        parts = line.split("\t")[-1].split("|", 3)
        if len(parts) != 4 or parts[0] != "dev" or not parts[1].isdigit():
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
