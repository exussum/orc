from datetime import datetime


def arrival(key: str, iata: str, when: datetime, airport: str | None, timeout: int) -> tuple[datetime, str, str | None]:
    return when, airport or "Test Airport", "1"
