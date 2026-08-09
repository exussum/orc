device define Light
device add Light LAMP h1 --room Bedroom
device add Light DESK h2
device seal Light

device only Chromecast CC host3 --room Living

routine define ROUTINE_RESET   Reset
routine append ROUTINE_RESET   Light         off --trigger SYSTEM
routine append .               Chromecast.CC stop
routine define ROUTINE_DEFAULT Welcome
routine append ROUTINE_DEFAULT Light         on

theme 'work day' ROUTINE_RESET   1:00
theme 'day off'  ROUTINE_DEFAULT sunset

room Bedroom Light.LAMP on

volume INFO  4
volume FATAL 10
