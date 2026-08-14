from datetime import date
from functools import lru_cache
from typing import Any

import requests

from orc import config


def market_holiday(today: date) -> bool:
    return any(
        e["date"] == today.strftime("%Y-%m-%d") and e["exchange"] == "NYSE" and e["status"] == "closed" for e in _fetch_holidays(today.year)
    )


@lru_cache(maxsize=2)
def _fetch_holidays(year: int) -> Any:
    result = requests.get(config.secrets.market_holidays_url, timeout=config.settings.http_timeout).json()
    if "error" in result:
        raise RuntimeError(result["error"])
    return result
