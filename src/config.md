##### Devices

| Type       | ID           | Hubitat Name     |
|------------|--------------|------------------|
| Light      | BEDROOM_LAMP | bedroom lamp     |
|            | LIVING_ROOM  | living room desk |
| Chromecast | LIVING_ROOM  | Living room mini |

---

##### Routines

| ID                 | Name       | Expression | State | Trigger |
|--------------------|------------|------------|-------|---------|
| ROUTINE_RESET      | Reset      | Light      | off   | System  |
| ROUTINE_LIGHTS_ON  | Lights On  | Light      | on    | System  |
| ROUTINE_LIGHTS_OFF | Lights Off | Light      | off   | System  |
| ROUTINE_QUIET      | Quiet      | Chromecast | stop  | System  |

---

##### People

| Name | Hostname |
|------|----------|

---

##### Themes

| Name     | ID                 | Time    |
|----------|--------------------|---------|
| work day | ROUTINE_RESET      | 1:00    |
|          | ROUTINE_LIGHTS_ON  | sunset  |
|          | ROUTINE_LIGHTS_OFF | sunrise |
| day off  | ROUTINE_QUIET      | 23:00   |

---

##### Room Configs

| Room        | IDs                | State |
|-------------|--------------------|-------|
| Living Room | Light.LIVING_ROOM  | on    |
| Bedroom     | Light.BEDROOM_LAMP | on    |

---

##### Ad-Hoc Routines

| Theme   | Expression | State |
|---------|------------|-------|
| Silence | Chromecast | stop  |

---

##### Plugins

| Name           | Expression     |
|----------------|----------------|
| All Lights On  | all_lights_on  |
| All Lights Off | all_lights_off |

---

### Button Highlights

| Name    | Start | End   |
|---------|-------|-------|
| Silence | 21:00 | 23:59 |

---

### Durations

|         |   |
|---------|---|
| Silence | 0 |
