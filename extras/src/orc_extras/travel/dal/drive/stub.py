from orc_extras.travel.dal.sqlite import Connection


def drive_minutes(connection: Connection, key: str, origin: str, dest: str, timeout: int) -> int:
    return 30


def geocode(connection: Connection, key: str, address: str, timeout: int) -> bool:
    return True
