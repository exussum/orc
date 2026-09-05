from typing import NamedTuple


class Settings(NamedTuple):
    hostname: str  # advertised api host, e.g. common.lgthinq.com
    https_advertise: int  # port advertised for the api server (nginx :443)
    mqtt_host: str  # ip the device connects to for mqtt, e.g. 192.168.4.6
    mqtt_port: int  # local plain listener the proxy client uses
    mqtts_advertise: int  # tls port the device connects to (broker)
    capture: bool = False


_current: Settings | None = None


def set_current(settings: Settings) -> None:
    global _current
    _current = settings


def current() -> Settings:
    if _current is None:
        raise RuntimeError("lg_ac plugin not set up")
    return _current
