"""The hub's past-log scrape over plain HTTP. Everything else (state, events,
commands, discovery) moved to the hub's MQTT export in orc.dal.mqtt."""

import re
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import requests

import orc
from orc import model as m

REQUIRED_SECRETS: dict[str, Callable[[str], Any]] = {}

_STATS_WINDOW = timedelta(days=2)
_FAILED = "failed after 5 retries"
_RETRIED = "succeeded on retry"
_STATE_CHANGE = re.compile(r"(?:was turned (?:on|off)(?: \[digital\])?|level was set to \d+%)$")


def fetch_retry_stats() -> tuple[m.RetryStats, ...]:
    base = urlparse(orc.config.settings.hubitat_url or "")
    resp = requests.get(f"{base.scheme}://{base.netloc}/logs/past/json", timeout=orc.config.settings.http_timeout)
    resp.raise_for_status()
    cutoff = datetime.now(orc.config.settings.tz).replace(tzinfo=None) - _STATS_WINDOW
    failed: Counter[str] = Counter()
    retried: Counter[str] = Counter()
    changes: Counter[str] = Counter()
    for line in resp.json():
        fields = line.split("\t")
        parts = fields[-1].split("|", 3)
        if len(parts) != 4 or parts[0] != "dev" or not parts[1].isdigit():
            continue
        elif datetime.fromisoformat(fields[0]) < cutoff:
            continue
        name, msg = parts[2], parts[3]
        if _FAILED in msg:
            failed[name] += 1
        elif _RETRIED in msg:
            retried[name] += 1
        elif _STATE_CHANGE.search(msg):
            changes[name] += 1
    return tuple(
        m.RetryStats(name=n, failed=failed[n], clean=max(changes[n] - retried[n], 0), retried=retried[n])
        for n in sorted({*failed, *retried, *changes})
    )
