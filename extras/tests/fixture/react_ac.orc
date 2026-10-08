react 'Patio fan' AC.living turns on if 'Eq(Sensor.living.contact, "open")' set AC fan_only:low --delay=1
react 'Fan off' AC.living turns fan_only:low if 'AcIs(AC.living, "cool:low:72")' set AC off
