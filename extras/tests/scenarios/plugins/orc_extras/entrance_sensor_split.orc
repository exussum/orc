setting cleanup_delay_minutes  2
setting presence_delay_minutes 1
setting entrance               Sensor.ENTRANCE_SENSOR
setting patio_door             Sensor.PATIO_DOOR
setting active_event           active
setting inactive_event         inactive
setting snapshot               45
setting listener               Rex

message log_present   'Trigger sensor off: skip (people present)'
message log_door_open 'Trigger sensor off: skip (patio door open)'
message log_absent    'Trigger sensor off: skip (listener home)'
message log_shutdown  'Trigger sensor off: applying OFF'
message log_nobody    'Entrance motion with nobody tracked before or after'

rules inside   AD_HOC_ALL_LIGHTS_OFF
rules present  AD_HOC_SILENCE
rules absent   AD_HOC_DOG
rules shutdown AD_HOC_ALL_LIGHTS_OFF

timed Day   8:00  22:00 AD_HOC_ALL_LIGHTS_ON
timed Night 22:00 8:00  AD_HOC_ALL_LIGHTS_OFF
