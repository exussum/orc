react Light turns on set off --delay=10
react Light.desk turns on set AC cool:low:75 if AC is on
react Light.desk turns off set off if AC is cool
react Light.lamp turns off set cool:low:75
react Light.lamp turns on set off if Light.desk is on
react Light.lamp turns on set off if Chromecast.tv is playing
