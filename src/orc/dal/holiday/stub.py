from datetime import date

from orc.dal import warn_stub

warn_stub("holiday")


def market_holiday(today: date) -> bool:
    return False
