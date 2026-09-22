setting cleanup_delay_minutes 2
setting entrance              Sensor.entrance
setting patio_door            Sensor.patio
setting active_event          active
setting inactive_event        inactive
setting snapshot              45
setting listener              Rex

message log_present   'Trigger sensor off: skip (people present)'
message log_door_open 'Trigger sensor off: skip (patio door open)'
message log_absent    'Trigger sensor off: skip (listener home)'
message log_shutdown  'Trigger sensor off: applying OFF'

rules inside   'Lights Off'
rules present  Silence
rules absent   Dog
rules shutdown ROUTINE_RESET

timed Day   8:00  22:00 'Day Scene'
timed Night 22:00 8:00  'Night Scene'
