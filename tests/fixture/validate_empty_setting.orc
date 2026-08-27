setting base_url        http://orc.test
setting lan_domain      orc.test
setting jobs_db         sqlite:////tmp/jobs.sqlite
setting lat             40.7143
setting long            -74.0060
setting audio_device    'USB Audio'
setting broadlink_codes /etc/orc/codes.json
setting mqtt_host       ''
setting announce_device Chromecast.CC

provider secrets    orc.dal.secrets.stub
provider weather    orc.dal.weather.stub
provider holiday    orc.dal.holiday.stub
provider mqtt       orc.dal.mqtt.stub
provider chromecast orc.dal.chromecast.stub
provider blaster    orc.dal.blaster.stub
provider hubitat    orc.dal.hubitat.stub

device define Light
device add Light LAMP h1
device seal Light

device only Chromecast CC host1

routine define ROUTINE_RESET   Reset
routine append ROUTINE_RESET   Light off --trigger SYSTEM
routine define ROUTINE_DEFAULT Welcome
routine append ROUTINE_DEFAULT Light on

theme 'work day' ROUTINE_RESET   1:00
theme 'day off'  ROUTINE_DEFAULT sunset

volume INFO  4
volume FATAL 10
