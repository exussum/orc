react 'Lights off' Light turns on set off --delay=15 --pause=30
react 'Cool' Sensor.LIVING_ROOM temperature between 68 and 75 set AC cool:low:72
react 'Dry' Sensor.LIVING_ROOM dewpoint(temperature,humidity) between 50 and 60 present alice,bob set AC fan_only:low:70
react 'Dry off' Sensor.LIVING_ROOM dewpoint(temperature,humidity) between 0 and 55 set AC off if AC is on
react Arrive Sensor.ENTRANCE_SENSOR turns active set Light      on
react Arrive Sensor.ENTRANCE_SENSOR turns active set Chromecast pause
