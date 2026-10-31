"""The activity log: the newest rows, each a trigger and the lines it caused."""

import contextlib
import threading
from collections import deque
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta

from orc import model as m

type Notify = Callable[[m.LogSourceEnum, str, m.Trigger, m.Notification], None]


class Message:
    BOOT: str = "Boot"
    BOOT_STEP: str = "{name} — {seconds}s"
    BOOT_TOTAL: str = "started in {seconds}s"

    SNAPSHOT_TAKEN: str = "Snapshot for `{name}` until {end:%I:%M}: {items}"
    SNAPSHOT_ALL_OFF: str = "all off"
    SNAPSHOT_RESTORE: str = "Restore Snapshot"
    SNAPSHOT_RESTORED: str = "Snapshot for `{name}` restored"

    THEME_OVERRIDE_CLEARED: str = "Theme override cleared"
    THEME_OVERRIDE_SET: str = "Theme override set: `{name}` {start}..{end}"

    LIGHT_TEST_STILL_ON: str = "Light test: still on: `{names}`"
    LIGHT_TEST_STILL_OFF: str = "Light test: still off: `{names}`"
    SOUND_TEST_TEXT: str = "Test"

    RULE_SKIPPED: str = "skipped: {detail}"
    RULE_SUPPRESSED: str = "Suppressed by snapshot: {kinds}"
    DISPATCH_FAILED: str = "Dispatch failed for `{device}`: {exc}"
    VIRTUAL_DEVICE_SKIPPED: str = "Skipped `{device}`: virtual device, nothing to dispatch"

    PRESENCE_SCAN_FAILED: str = "Presence scan failed for `{name}`: {exc}"
    PRESENCE_DETECTED: str = "Presence detected: `{name}`"
    PRESENCE_LOST: str = "Presence lost: `{name}`"
    PRESENCE_EXPIRED: str = "Presence expired: `{name}`"
    PRESENCE_CHECKED_IN: str = "Presence checked in: `{name}`"
    PRESENCE_RESCAN: str = "Presence rescan"

    TAG_KEY_UPDATED: str = "Tag key for `{name}` read from Find Hub: {fingerprint}, paired {paired:%Y-%m-%d %H:%M}"
    TAG_KEY_FAILED: str = "Find Hub lookup failed for `{name}`: {exc}"

    TASK_QUEUED: str = "Queued: `{id}` (until {when:%I:%M})"
    BUTTON_ACTION_UNKNOWN: str = "Unknown button action: `{id}`"
    LOW_BATTERY: str = "Low battery on `{device}` ({level})"
    ROOM_SET: str = "Room: `{id}` {state}"
    DEVICE_SET: str = "`{name}` {what}"
    DEVICE_SET_VOLUME: str = "volume {level}"
    DEVICE_SET_LEVEL: str = "level {level}"

    EXTERNAL_CHANGE: str = "`{device}` {attribute}: {old} → {new}"
    CONFLICTING_ARMS: str = "`{device}` given conflicting states in one run: {states}"

    ANNOUNCE: str = "Announce: {text}"

    PUSH_TITLE: str = "[{source} {date}]"
    PUSH_FAILED: str = "Push failed for `…{endpoint}`: {exc}"
    PUSH_GREETING: str = "Notifications enabled on this device"


class Log:
    ROLLUP_WINDOW = timedelta(seconds=5)
    SIZE = 200

    def __init__(self, now: Callable[[], datetime], notify: Notify) -> None:
        self._now = now
        self._notify = notify
        self._lock = threading.Lock()
        self._rows: deque[m.LogEntry] = deque(maxlen=self.SIZE)
        self._open_rows: list[m.LogEntry] = []

    def write(
        self,
        source: m.LogSourceEnum,
        action: str,
        trigger: m.Trigger,
        *,
        notification: m.Notification | None = None,
        link: str | None = None,
    ) -> m.LogEntry:
        now = self._now()
        with self._lock:
            row = self._row_for(trigger, now)
            if row:
                row.add(source, action, notified=bool(notification), link=link)
                row.notified = row.notified or bool(notification)
            else:
                row = m.LogEntry(now, source, action, trigger, notified=bool(notification), link=link)
                self._rows.appendleft(row)
        if notification:
            self._notify(source, action, row.trigger, notification)
        return row

    def rows(self) -> list[m.LogEntry]:
        with self._lock:
            return list(self._rows)

    def open_row(self, row: m.LogEntry) -> None:
        with self._lock:
            self._open_rows.append(row)

    def close_row(self, row: m.LogEntry) -> None:
        with self._lock:
            self._open_rows.remove(row)

    @contextlib.contextmanager
    def opened(self, row: m.LogEntry) -> Iterator[None]:
        self.open_row(row)
        try:
            yield
        finally:
            self.close_row(row)

    def clear(self) -> None:
        with self._lock:
            self._rows.clear()
            self._open_rows.clear()

    def _row_for(self, trigger: m.Trigger, now: datetime) -> m.LogEntry | None:
        fresh = (row for row in self._rows if now - row.touched < self.ROLLUP_WINDOW)
        return next((row for row in (*self._open_rows, *fresh) if row.trigger == trigger), None)
