class Log:
    BOOT: str = "Boot"

    SNAPSHOT_TAKEN: str = "Snapshot '{name}' until {end:%H:%M}: {items}"
    SNAPSHOT_ALL_OFF: str = "all off"
    SNAPSHOT_RESTORED: str = "Snapshot '{name}' restored"

    THEME_OVERRIDE_CLEARED: str = "Theme override cleared"
    THEME_OVERRIDE_SET: str = "Theme override set: {name} {start}..{end}"

    RULE_SKIPPED: str = "Skipped {rule_name} ({detail})"
    RULE_SUPPRESSED: str = "Suppressed by snapshot: {kinds}"

    PRESENCE_PING_FAILED: str = "Presence ping failed for {name}: {exc}"
    PRESENCE_DETECTED: str = "Presence detected: {name}"
    PRESENCE_LOST: str = "Presence lost: {name}"
    PRESENCE_EXPIRED: str = "Presence expired: {name}"
    PRESENCE_CHECKED_IN: str = "Presence checked in: {name}"

    VERSION_MISMATCH: str = "Version mismatch: client={client} server={server}"

    TASK_QUEUED: str = "Queued: {id} (until {when:%H:%M})"
    JOB_FORCED: str = "Force run: {job_name}"
    ROOM_SET: str = "Room: {id} {state}"

    YOLINK_CONNECTED: str = "YoLink {name} connected"
    YOLINK_DISCONNECTED: str = "YoLink {name} disconnected"
    YOLINK_WATER_DETECTED: str = "Water detected in {name}"
    YOLINK_WATER_CLEARED: str = "Water cleared in {name}"
    YOLINK_LOW_BATTERY: str = "Low battery on {name} ({battery}/4)"
    YOLINK_BATTERY_RESTORED: str = "Battery restored on {name} ({battery}/4)"
    YOLINK_WEAK_SIGNAL: str = "Weak signal on {name} ({signal} dBm)"
    YOLINK_SIGNAL_RESTORED: str = "Signal restored on {name} ({signal} dBm)"
    YOLINK_INTERVAL_CHANGED: str = "Report interval for {name} changed to {interval}s"
    YOLINK_OFFLINE: str = "{name} offline"
    YOLINK_ONLINE: str = "{name} online"
