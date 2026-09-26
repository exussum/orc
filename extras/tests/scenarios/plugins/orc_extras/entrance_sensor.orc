setting cleanup_delay_minutes 2
setting entrance              Sensor.ENTRANCE_SENSOR
setting patio_door            Sensor.PATIO_DOOR
setting active_event          active
setting inactive_event        inactive
setting snapshot              45
setting listener              Rex

message log_present   'Trigger sensor off: skip (people present)'
message log_door_open 'Trigger sensor off: skip (patio door open)'
message log_absent    'Trigger sensor off: skip (listener home)'
message log_shutdown  'Trigger sensor off: applying OFF'

rules inside   'All Lights Off'
rules present  Silence
rules absent   Dog
rules shutdown 'All Lights Off'

timed Day   8:00  22:00 'All Lights On'
timed Night 22:00 8:00  'All Lights Off'
