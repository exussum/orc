react 'Patio fan' AC.living turns on if 'Eq(Sensor.living.contact, "open")' set AC ac:fan_only:low --delay=60
react 'Fan off' AC.living turns ac:fan_only:low if 'AcIs(AC.living, "ac:cool:low:72")' set AC off
