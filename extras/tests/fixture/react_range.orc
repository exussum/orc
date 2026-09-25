react Sensor.living temperature between 68 and 75 set AC cool:low:72
react Sensor.living dewpoint(temperature,humidity) between 50 and 60 present alice,bob set AC fan_only:low:70
react Sensor.living dewpoint(temperature,humidity) between 59 and 104 present ANYONE set AC off
react Sensor.living dewpoint(temperature,humidity) between 0 and 55 set AC off if AC is on
