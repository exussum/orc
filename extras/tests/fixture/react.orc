react 'Lights off' Light turns on set off --delay=10
react 'Desk cools' Light.desk turns on if 'AcIs(AC.living, "on")' set AC cool:low:75 --pause=30
react 'Desk stops AC' Light.desk turns off if 'AcIs(AC.living, "cool")' set off
react 'Lamp cools' Light.lamp turns off set cool:low:75
react 'Lamp off with desk' Light.lamp turns on if 'Eq(Light.desk.switch, "on")' set off
react 'Lamp off while playing' Light.lamp turns on if 'Eq(Chromecast.tv, "playing")' set off
react 'Motion lamp' Sensor.living turns active set Light.lamp on
