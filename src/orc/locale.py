class Log:
    BOOT = "Boot"

    SNAPSHOT_TAKEN = "Snapshot until {end:%H:%M}: {items}"
    SNAPSHOT_RESTORED = "Snapshot restored"

    THEME_OVERRIDE_CLEARED = "Theme override cleared"
    THEME_OVERRIDE_SET = "Theme override set: {name} {start}..{end}"

    RULE_SKIPPED = "Skipped {rule_name} ({detail})"

    PRESENCE_PING_FAILED = "Presence ping failed for {name}: {exc}"
    PRESENCE_DETECTED = "Presence detected: {name}"
    PRESENCE_LOST = "Presence lost: {name}"
    PRESENCE_EXPIRED = "Presence expired: {name}"
    PRESENCE_CHECKED_IN = "Presence checked in: {name}"

    JOB_FORCED = "Force run: {job_name}"
    ROOM_SET = "Room: {id} {state}"

    TRIGGER_SENSOR_OFF_PREFIX = "Trigger sensor off: {msg}"
    TRIGGER_SENSOR_OFF_SKIPPED_NIGHTTIME = "skip (nighttime)"
    TRIGGER_SENSOR_OFF_SKIPPED_PRESENT = "skip (present: {names})"
    TRIGGER_SENSOR_OFF_SKIPPED_SOUNDS = "skip (sounds playing)"
    TRIGGER_SENSOR_OFF_APPLIED = "applying OFF"

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
