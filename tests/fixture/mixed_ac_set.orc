device define Light
device add Light LAMP h1
device seal Light
device only AC UNIT clip-1
room R '{Light.LAMP, AC.UNIT}' off
