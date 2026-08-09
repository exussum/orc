setting entrance_id 1
setting snapshot    45

message log_present 'skip (people present)'

rules enter  Light      on
rules enter  Chromecast pause
rules inside Light      off

timed define Day 8:00 22:00
timed append Day Light 20
