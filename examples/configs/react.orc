react 'Lights off' Light turns on set off --delay=15 --pause=30
react 'Cool'       Sensor.LIVING_ROOM if 'Entered(temperature, 68, 75)' set AC cool:low:72
react 'Dry'        Sensor.LIVING_ROOM if 'And(Present("alice","bob"), Entered(dewpoint(temperature,humidity), 50, 60))' set AC fan_only:low:70
react 'Dry off'    Sensor.LIVING_ROOM if 'And(AcIs(AC.LIVING_ROOM, "on"), Entered(dewpoint(temperature,humidity), 0, 55))' set AC off
react Arrive Sensor.ENTRANCE_SENSOR turns active set Light      on
react Arrive Sensor.ENTRANCE_SENSOR turns active set Chromecast pause
