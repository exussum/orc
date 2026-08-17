from orc_plugins.travel.dal.sqlite import Connection


def drive_minutes(connection: Connection, key: str, origin: str, dest: str, timeout: int) -> int:
    return 30
