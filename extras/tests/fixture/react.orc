react 'Lights off' Light turns on set off --delay=10
react 'Desk cools' Light.desk turns on set AC cool:low:75 if AC is on --pause=30
react 'Desk stops AC' Light.desk turns off set off if AC is cool
react 'Lamp cools' Light.lamp turns off set cool:low:75
react 'Lamp off with desk' Light.lamp turns on set off if Light.desk is on
react 'Lamp off while playing' Light.lamp turns on set off if Chromecast.tv is playing
react 'Motion lamp' Sensor.living turns active set Light.lamp on
