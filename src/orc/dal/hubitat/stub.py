from orc import model as m
from orc.dal import warn_stub

warn_stub("hubitat")


def reboot() -> None:
    pass


def fetch_retry_stats() -> tuple[m.RetryStats, ...]:
    return ()
