provider secrets    orc.dal.secrets.stub
provider weather    orc.dal.weather.stub
provider holiday    orc.dal.holiday.stub
provider mqtt       orc.dal.mqtt.stub
provider chromecast orc.dal.chromecast.stub
provider calendar   orc.dal.calendar.stub
provider blaster    orc.dal.blaster.stub
provider hubitat    orc.dal.hubitat.stub

device define Light
device add Light LAMP h1
device seal Light

routine define ROUTINE_RESET   Reset
routine append ROUTINE_RESET   Light off --trigger SYSTEM
routine define ROUTINE_DEFAULT Welcome
routine append ROUTINE_DEFAULT Light on

theme 'work day' ROUTINE_RESET   1:00
theme 'day off'  ROUTINE_DEFAULT sunset

volume INFO  4
volume FATAL 10
