react 'Arrive AC' Sensor.ENTRANCE_SENSOR turns active   set AC cool:low:75
react 'Arrive light' Sensor.ENTRANCE_SENSOR turns active   set Light.LIVING_ROOM on
react 'Leave AC' Sensor.ENTRANCE_SENSOR turns inactive set AC off

react Bedtime Light.BEDROOM_LAMP turns on set Chromecast pause
react Bedtime Light.BEDROOM_LAMP turns on set Light.LIVING_ROOM on
react Bedtime Light.BEDROOM_LAMP turns on set Light.KITCHEN on
react Bedtime Light.BEDROOM_LAMP turns on set Light.HALL on
react Bedtime Light.BEDROOM_LAMP turns on set Light.PORCH off
react Bedtime Light.BEDROOM_LAMP turns on set Light.OFFICE off
react Bedtime Light.BEDROOM_LAMP turns on set Light.OFFICE on
