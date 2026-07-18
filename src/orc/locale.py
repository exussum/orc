class Log:
    BOOT: str = "Boot"

    SNAPSHOT_TAKEN: str = "Snapshot '{name}' until {end:%I:%M}: {items}"
    SNAPSHOT_ALL_OFF: str = "all off"
    SNAPSHOT_RESTORED: str = "Snapshot '{name}' restored"

    THEME_OVERRIDE_CLEARED: str = "Theme override cleared"
    THEME_OVERRIDE_SET: str = "Theme override set: {name} {start}..{end}"

    RULE_SKIPPED: str = "Skipped {rule_name} ({detail})"
    RULE_SUPPRESSED: str = "Suppressed by snapshot: {kinds}"
    DISPATCH_FAILED: str = "Dispatch failed for {device}: {exc}"

    PRESENCE_PING_FAILED: str = "Presence ping failed for {name}: {exc}"
    PRESENCE_DETECTED: str = "Presence detected: {name}"
    PRESENCE_LOST: str = "Presence lost: {name}"
    PRESENCE_EXPIRED: str = "Presence expired: {name}"
    PRESENCE_CHECKED_IN: str = "Presence checked in: {name}"

    VERSION_MISMATCH: str = "Version mismatch: client={client} server={server}"

    TASK_QUEUED: str = "Queued: {id} (until {when:%I:%M})"
    JOB_FORCED: str = "Force run: {job_name}"
    ROOM_SET: str = "Room: {id} {state}"
