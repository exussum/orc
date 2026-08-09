setting cleanup_delay_minutes 2
setting entrance_id           1
setting patio_door_id         56
setting active_event          active
setting inactive_event        inactive
setting snapshot              45

message log_present   'Trigger sensor off: skip (people present)'
message log_door_open 'Trigger sensor off: skip (patio door open)'
message log_absent    'Trigger sensor off: skip (sounds playing)'
message log_shutdown  'Trigger sensor off: applying OFF'

rules enter    Light      on
rules enter    Chromecast pause
rules inside   Light      off
rules present  Chromecast stop
rules absent   Chromecast resume
rules shutdown Light      off

timed define Day   8:00  22:00
timed append Day   Light 20
timed define Night 22:00 8:00
timed append Night Light 1
