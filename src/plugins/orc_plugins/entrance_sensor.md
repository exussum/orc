##### Settings

| Key                   | Value    |
|-----------------------|----------|
| cleanup_delay_minutes | 2        |
| entrance_id           | 1        |
| patio_door_id         | 56       |
| active_event          | active   |
| inactive_event        | inactive |
| snapshot              | 45       |

##### Messages

| Log            | Message                                     |
|----------------|---------------------------------------------|
| log_present    | Trigger sensor off: skip (people present)   |
| log_door_open  | Trigger sensor off: skip (patio door open)  |
| log_absent     | Trigger sensor off: skip (sounds playing)   |
| log_shutdown   | Trigger sensor off: applying OFF            |

##### Rules

| Trigger  | Device     | State  |
|----------|------------|--------|
| enter    | Light      | on     |
|          | Chromecast | pause  |
| inside   | Light      | off    |
| present  | Chromecast | stop   |
| absent   | Chromecast | resume |
| shutdown | Light      | off    |

##### Timed

| Name  | Start | Stop  | Device | State |
|-------|-------|-------|--------|-------|
| Day   | 8:00  | 22:00 | Light  | 20    |
| Night | 22:00 | 8:00  | Light  | 1     |
