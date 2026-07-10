class Log:
    BOOT = "Boot"

    SNAPSHOT_TAKEN = "Snapshot '{name}' until {end:%H:%M}: {items}"
    SNAPSHOT_ALL_OFF = "all off"
    SNAPSHOT_RESTORED = "Snapshot '{name}' restored"

    THEME_OVERRIDE_CLEARED = "Theme override cleared"
    THEME_OVERRIDE_SET = "Theme override set: {name} {start}..{end}"

    RULE_SKIPPED = "Skipped {rule_name} ({detail})"
    RULE_SUPPRESSED = "Suppressed by snapshot: {kinds}"

    PRESENCE_PING_FAILED = "Presence ping failed for {name}: {exc}"
    PRESENCE_DETECTED = "Presence detected: {name}"
    PRESENCE_LOST = "Presence lost: {name}"
    PRESENCE_EXPIRED = "Presence expired: {name}"
    PRESENCE_CHECKED_IN = "Presence checked in: {name}"

    VERSION_MISMATCH = "Version mismatch: client={client} server={server}"

    TASK_QUEUED = "Queued: {id} (until {when:%H:%M})"
    JOB_FORCED = "Force run: {job_name}"
    ROOM_SET = "Room: {id} {state}"

    YOLINK_CONNECTED = "YoLink {name} connected"
    YOLINK_DISCONNECTED = "YoLink {name} disconnected"
    YOLINK_WATER_DETECTED = "Water detected in {name}"
    YOLINK_WATER_CLEARED = "Water cleared in {name}"
    YOLINK_LOW_BATTERY = "Low battery on {name} ({battery}/4)"
    YOLINK_BATTERY_RESTORED = "Battery restored on {name} ({battery}/4)"
    YOLINK_WEAK_SIGNAL = "Weak signal on {name} ({signal} dBm)"
    YOLINK_SIGNAL_RESTORED = "Signal restored on {name} ({signal} dBm)"
    YOLINK_INTERVAL_CHANGED = "Report interval for {name} changed to {interval}s"
    YOLINK_OFFLINE = "{name} offline"
    YOLINK_ONLINE = "{name} online"
