react 'Cool' Sensor.living if 'Entered(temperature, 68, 75)' set AC cool:low:72
react 'Dry' Sensor.living if 'And(Present("alice","bob"), Entered(dewpoint(temperature,humidity), 50, 60))' set AC fan_only:low:70
react 'Muggy off' Sensor.living if 'And(Anyone(), Entered(dewpoint(temperature,humidity), 59, 104))' set AC off
react 'Dry off' Sensor.living if 'And(AcIs(AC.living, "on"), Entered(dewpoint(temperature,humidity), 0, 55))' set AC off
react 'Warm out' Sensor.living if 'And(Entered(temperature, 75, 100), Range(Outside.temperature, 66, 120))' set AC cool:low:77
