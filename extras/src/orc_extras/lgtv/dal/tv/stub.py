from orc.model import DeviceEnum
from orc_extras.lgtv.dal.sqlite import Connection


def pair(connection: Connection, hostname: str) -> str | None:
    return None


def is_off(tv: DeviceEnum) -> bool:
    return True


def off(connection: Connection, tv: DeviceEnum) -> None:
    pass
