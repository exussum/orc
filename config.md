# Devices

| Type  | ID                   | Hubitat Name           |
|-------|----------------------|------------------------|
| Light | BEDROOM_NIGHTLIGHT   | bedroom night light    |
|       | BEDROOM_LAMP         | bedroom lamp           |
|       | ENTANCE_DESK         | entrance desk lamp     |
|       | ENTRANCE_BULB_1      | entrance bulb 1        |
|       | ENTRANCE_BULB_2      | entrance bulb 2        |
|       | LIVING_ROOM_DESK     | living room desk lamp  |
|       | LIVING_ROOM_FLOOR    | living room floor lamp |
|       | KITCHEN_CABINET      | kitchen lights         |
|       | KITCHEN_OVERHEAD     | kitchen overhead       |
|       | OFFICE_FLOOR         | office floor lamp      |
|       | OFFICE_DESK          | office desk lamp       |
|       | OFFICE_TABLE         | office table lamp      |
| Sound | LIVING_ROOM_MINI     | Living room mini       |
|       | BEDROOM_DISPLAY      | Bedroom display        |
|       | OFFICE_DISPLAY       | Office display         |
|       | KITCHEN_CABINET_MINI | Kitchen mini           |

# Routines

| ID                      | Name            | Expression                                        | State | Mandatory |
|-------------------------|-----------------|---------------------------------------------------|-------|-----------|
| ROUTINE_RESET_LIGHT     | Reset           | Light - {Light.BEDROOM_NIGHTLIGHT}                | off   | True      |
| ROUTINE_PARTNER_UP      | Partner up      | {Light.LIVING_ROOM_FLOOR, Light.KITCHEN_CABINET}  | on    |           |
| ROUTINE_SUNRISE_LIGHTS  | Sunrise Lights  | {Light.BEDROOM_NIGHTLIGHT, Light.KITCHEN_CABINET} | off   | True      |
| ROUTINE_UP_AND_ATOM     | Up and Atom     | {Light.ENTANCE_DESK, Light.OFFICE_TABLE}          | 100   |           |
|                         |                 | {Light.LIVING_ROOM_DESK, Light.LIVING_ROOM_FLOOR} | on    |           |
|                         |                 | Sound                                             | 40    |           |
|                         |                 | {Light.OFFICE_TABLE, Light.BEDROOM_NIGHTLIGHT}    | off   |           |
| ROUTINE_SUNSET_LIGHTS   | Sunset Lights   | {Light.BEDROOM_NIGHTLIGHT, Light.KITCHEN_CABINET} | on    | True      |
| ROUTINE_QUIET_TIME      | Quiet Time      | Sound                                             | 10    | True      |
| ROUTINE_PARTNER_LEAVING | Partner Leaving | Light.ENTANCE_DESK                                | 1     |           |
| ROUTINE_NIGHTLIGHT_OFF  | Nightlight Off  | Light.BEDROOM_NIGHTLIGHT                          | off   |           |
| ROUTINE_NIGHTLIGHT_ON   | Nightlight On   | Light.BEDROOM_NIGHTLIGHT                          | on    |           |

# Themes

| Name       | ID                      | Time    |
|------------|-------------------------|---------|
| work day   | ROUTINE_RESET_LIGHT     | 1:00    |
|            | ROUTINE_PARTNER_UP      | 6:15    |
|            | ROUTINE_PARTNER_LEAVING | 7:00    |
|            | ROUTINE_SUNRISE_LIGHTS  | sunrise |
|            | ROUTINE_UP_AND_ATOM     | 9:00    |
|            | ROUTINE_SUNSET_LIGHTS   | sunset  |
|            | ROUTINE_QUIET_TIME      | 23:00   |
| away       | ROUTINE_RESET_LIGHT     | 1:00    |
| babysitter | ROUTINE_NIGHTLIGHT_OFF  | sunrise |
|            | ROUTINE_NIGHTLIGHT_ON   | sunset  |
| home alone | ROUTINE_RESET_LIGHT     | 1:00    |
|            | ROUTINE_SUNRISE_LIGHTS  | sunrise |
|            | ROUTINE_UP_AND_ATOM     | 9:30    |
|            | ROUTINE_SUNSET_LIGHTS   | sunset  |
|            | ROUTINE_QUIET_TIME      | 23:00   |
| day off    | ROUTINE_RESET_LIGHT     | 1:00    |
|            | ROUTINE_PARTNER_UP      | 7:00    |
|            | ROUTINE_SUNRISE_LIGHTS  | sunrise |
|            | ROUTINE_UP_AND_ATOM     | 9:30    |
|            | ROUTINE_SUNSET_LIGHTS   | sunset  |
|            | ROUTINE_QUIET_TIME      | 23:00   |

# Room Configs

| Room        | IDs                                               | State |
|-------------|---------------------------------------------------|-------|
| Living Room | {Light.LIVING_ROOM_FLOOR, Light.LIVING_ROOM_DESK} | on    |
|             | Light.ENTANCE_DESK                                | 100   |
| Office      | Light.OFFICE_FLOOR                                | on    |
|             | Light.OFFICE_DESK                                 | 35    |
|             | Light.OFFICE_TABLE                                | 100   |
| Kitchen     | {Light.KITCHEN_CABINET, Light.KITCHEN_OVERHEAD}   | on    |
| Bedroom     | Light.BEDROOM_LAMP                                | on    |


# Ad-Hoc Routines

| Theme                | Expression                                                                                                                 | State |
|----------------------|----------------------------------------------------------------------------------------------------------------------------|-------|
| Bed Time             | Light - {Light.BEDROOM_NIGHTLIGHT}                                                                                         | off   |
|                      | Light.BEDROOM_NIGHTLIGHT                                                                                                   | on    |
| Partial TV Lights    | Light - {Light.ENTANCE_DESK, Light.OFFICE_TABLE, Light.KITCHEN_CABINET, Light.LIVING_ROOM_FLOOR, Light.BEDROOM_NIGHTLIGHT} | off   |
|                      | {Light.LIVING_ROOM_FLOOR, Light.KITCHEN_CABINET}                                                                           | on    |
|                      | {Light.ENTANCE_DESK, Light.OFFICE_TABLE}                                                                                   | 1     |
| TV Lights            | Light - {Light.ENTANCE_DESK, Light.OFFICE_TABLE, Light.KITCHEN_CABINET, Light.BEDROOM_NIGHTLIGHT}                          | off   |
|                      | Light.KITCHEN_CABINET                                                                                                      | on    |
|                      | {Light.ENTANCE_DESK, Light.OFFICE_TABLE}                                                                                   | 1     |
| Early Morning Lights | {Light.LIVING_ROOM_FLOOR, Light.KITCHEN_CABINET}                                                                           | on    |

# Super Routines

| Name             | Expression                                                                  |
|------------------|-----------------------------------------------------------------------------|
| All Lights On    | Config(Light, "on"),                                                        |
|                  | Config(Light, 100),                                                         |
| All Lights Off   | Config(Light, "off"),                                                       |
| Video Conference | Config(Light.OFFICE_TABLE, 5),                                              |
|                  | Config(Light.OFFICE_FLOOR, "on"),                                           |
|                  | Config(Light.OFFICE_DESK, 50),                                              |
| Test             | (Config(e, s) for (e, s) in tuple(itertools.product(Light, ["on", "off"]))) |
| Restore Snapshot |                                                                             |
| Back on Schedule |                                                                             |
