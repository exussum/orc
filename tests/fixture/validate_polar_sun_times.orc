setting base_url         http://orc.test
setting lan_domain       orc.test
setting jobs_db          sqlite:////tmp/jobs.sqlite
setting lat              70.0
setting long             -74.0060
setting broadlink_codes  /etc/orc/codes.json
setting mqtt_host        hub.test
setting warning_device   USB.AUDIO
setting attention_device USB.AUDIO
setting emergency_device USB.AUDIO
setting emergency_routine ROUTINE_RESET

provider secrets    orc.dal.secrets.stub
provider weather    orc.dal.weather.stub
provider holiday    orc.dal.holiday.stub
provider mqtt       orc.dal.mqtt.stub
provider chromecast orc.dal.chromecast.stub
provider blaster    orc.dal.blaster.stub
provider hubitat    orc.dal.hubitat.stub
provider audio      orc.dal.audio.stub

device only USB AUDIO 'USB Audio'

routine define ROUTINE_RESET   Reset
routine define ROUTINE_DEFAULT Welcome

theme 'work day' ROUTINE_RESET   1:00
theme 'day off'  ROUTINE_DEFAULT sunset
