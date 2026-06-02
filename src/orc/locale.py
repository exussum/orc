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

    JOB_FORCED = "Force run: {job_name}"
    ROOM_SET = "Room: {id} {state}"

    ENTRANCE_SENSOR_TRIGGERED = "Entrance sensor"

    TRIGGER_SENSOR_OFF_PREFIX = "Trigger sensor off: {msg}"
    TRIGGER_SENSOR_OFF_SKIPPED_NIGHTTIME = "skip (nighttime)"
    TRIGGER_SENSOR_OFF_SKIPPED_PRESENT = "skip (present: {names})"
    TRIGGER_SENSOR_OFF_SKIPPED_SOUNDS = "skip (sounds playing)"
    TRIGGER_SENSOR_OFF_APPLIED = "applying OFF"
