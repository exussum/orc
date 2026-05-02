##### Devices

| Type  | ID            | Hubitat Name        |
|-------|---------------|---------------------|
| Light | BEDROOM_LAMP  | bedroom lamp        |
|       | LIVING_ROOM   | living room desk    |
| Sound | LIVING_ROOM   | Living room mini    |

---

##### Routines

| ID                 | Name       | Expression | State | Mandatory |
|--------------------|------------|------------|-------|-----------|
| ROUTINE_RESET      | Reset      | Light      | off   | True      |
| ROUTINE_LIGHTS_ON  | Lights On  | Light      | on    | True      |
| ROUTINE_LIGHTS_OFF | Lights Off | Light      | off   | True      |
| ROUTINE_QUIET      | Quiet      | Sound      | stop  | True      |

---

##### Themes

| Name    | ID                 | Time    |
|---------|--------------------|---------|
| default | ROUTINE_RESET      | 1:00    |
|         | ROUTINE_LIGHTS_ON  | sunset  |
|         | ROUTINE_LIGHTS_OFF | sunrise |
|         | ROUTINE_QUIET      | 23:00   |

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
| Silence | Sound      | stop  |

---

##### Super Routines

| Name           | Expression            |
|----------------|-----------------------|
| All Lights On  | Config(Light, "on"),  |
| All Lights Off | Config(Light, "off"), |

---

### Button Highlights

| Name    | Start | End   |
|---------|-------|-------|
| Silence | 21:00 | 23:59 |
