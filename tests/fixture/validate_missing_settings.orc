provider secrets    orc.dal.secrets.stub
provider weather    orc.dal.weather.stub
provider holiday    orc.dal.holiday.stub
provider mqtt       orc.dal.mqtt.stub
provider chromecast orc.dal.chromecast.stub
provider blaster    orc.dal.blaster.stub
provider hubitat    orc.dal.hubitat.stub
provider audio      orc.dal.audio.stub

routine define ROUTINE_RESET   Reset
routine define ROUTINE_DEFAULT Welcome

theme 'work day' ROUTINE_RESET   1:00
theme 'day off'  ROUTINE_DEFAULT sunset
