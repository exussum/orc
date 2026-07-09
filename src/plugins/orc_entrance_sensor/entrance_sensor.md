##### Settings

| Key                   | Value    |
|-----------------------|----------|
| cleanup_delay_minutes | 2        |
| entrance_id           | 1        |
| active_event          | active   |
| inactive_event        | inactive |
| day_start             | 8        |
| day_end               | 22       |
| snapshot              | 45       |

##### Messages

| Log             | Message                                   |
|-----------------|-------------------------------------------|
| log_after_hours | Trigger sensor off: skip (nighttime)      |
| log_present     | Trigger sensor off: skip (people present) |
| log_core_hours  | Trigger sensor off: skip (sounds playing) |
| log_shutdown    | Trigger sensor off: applying OFF          |

##### Day

| Trigger            | Device     | State  |
|--------------------|------------|--------|
| entrance_light_on  | Light      | 20     |
| entrance_light_off | Light      | off    |
| entrance_config    | Light      | on     |
|                    | Chromecast | pause  |
| after_hours        | Chromecast | stop   |
| core_hours         | Chromecast | resume |
| shutdown           | Light      | off    |

##### Night

| Trigger            | Device     | State |
|--------------------|------------|-------|
| entrance_light_on  | Light      | 1     |
| entrance_light_off | Light      | off   |
| entrance_config    | Light      | on    |
|                    | Chromecast | pause |
| after_hours        | Chromecast | stop  |
| core_hours         | Chromecast | stop  |
| shutdown           | Light      | off   |
