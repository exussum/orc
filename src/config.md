##### Devices

| Type       | Name         | Room    | Host             |
|------------|--------------|---------|------------------|
| Light      | BEDROOM_LAMP | Bedroom | bedroom lamp     |
|            | LIVING_ROOM  |         | living room desk |
| Chromecast | LIVING_ROOM  | Living  | Living room mini |

---

##### Routines

| ID                 | Name       | Device     | State | Trigger |
|--------------------|------------|------------|-------|---------|
| ROUTINE_RESET      | Reset      | Light      | off   | SYSTEM  |
| ROUTINE_LIGHTS_ON  | Lights On  | Light      | on    | SYSTEM  |
| ROUTINE_LIGHTS_OFF | Lights Off | Light      | off   | SYSTEM  |
| ROUTINE_QUIET      | Quiet      | Chromecast | stop  | SYSTEM  |
| ROUTINE_DEFAULT    | Welcome    | Light      | on    | SYSTEM  |

---

##### People

| Name | Hostname | MAC |
|------|----------|-----|

---

##### Themes

| Name     | Routine            | Time    |
|----------|--------------------|---------|
| work day | ROUTINE_RESET      | 1:00    |
|          | ROUTINE_LIGHTS_ON  | sunset  |
|          | ROUTINE_LIGHTS_OFF | sunrise |
| day off  | ROUTINE_QUIET      | 23:00   |

---

##### Room Configs

| Room        | Device             | State |
|-------------|--------------------|-------|
| Living Room | Light.LIVING_ROOM  | on    |
| Bedroom     | Light.BEDROOM_LAMP | on    |

---

##### Ad-Hoc Routines

| Name           | Device     | State | Parameters  |
|----------------|------------|-------|-------------|
| Silence        | Chromecast | stop  | reset=false |
| Dog            | Chromecast | stop  | delay=7     |
| All Lights On  | Light      | 100   | reset=false |
| All Lights Off | Light      | off   | reset=false |

---

##### Plugins

| Name             | Plugin                                             | Parameters      |
|------------------|----------------------------------------------------|-----------------|
| Entrance Sensor  | orc_plugins.entrance_sensor.plugins.trigger_sensor | section=hubitat |
| Pair LG TV       | orc_plugins.lgtv.plugins.pair_tv                   | section=device  |
| Test Leak Sensor | orc_plugins.yolink.plugins.test_sensor             | section=device  |

---

### Button Highlights

| Name    | Start | End   |
|---------|-------|-------|
| Silence | 21:00 | 23:59 |

---

### Audio Volumes

| Level | Volume |
|-------|--------|
| INFO  | 4      |
| FATAL | 10     |

---

### Durations

|         |   |
|---------|---|
| Silence | 0 |
