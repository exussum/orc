setting base_url        http://orc.internal.example
setting lan_domain      orc.internal.example
setting jobs_db         sqlite:////tmp/jobs.sqlite
setting lat             42.4440
setting long            -76.5019
setting audio_device    'Speakers'
setting broadlink_codes /etc/orc/broadlink_codes.json
setting mqtt_host       hubitat.example

provider secrets    orc.dal.secrets.stub
provider weather    orc.dal.weather.stub
provider holiday    orc.dal.holiday.stub
provider mqtt       orc.dal.mqtt.stub
provider chromecast orc.dal.chromecast.stub
provider blaster    orc.dal.blaster.stub
provider hubitat    orc.dal.hubitat.stub

device define Light
device add Light BEDROOM_LAMP 'bedroom lamp'     --room Bedroom
device add Light LIVING_ROOM  'living room desk'
device seal Light

device only Chromecast LIVING_ROOM        'Living room mini' --room Living
device only Button     LIVING_ROOM_REMOTE scene              --room Living
device only BroadLink
device only AC
device only LGTV
device only WebOS
device only Leak

routine define ROUTINE_RESET      Reset
routine append ROUTINE_RESET      Light      off  --trigger SYSTEM
routine define ROUTINE_LIGHTS_ON  'Lights On'
routine append ROUTINE_LIGHTS_ON  Light      on   --trigger SYSTEM
routine define ROUTINE_LIGHTS_OFF 'Lights Off'
routine append ROUTINE_LIGHTS_OFF Light      off  --trigger SYSTEM
routine define ROUTINE_QUIET      Quiet
routine append ROUTINE_QUIET      Chromecast stop --trigger SYSTEM
routine define ROUTINE_DEFAULT    Welcome
routine append ROUTINE_DEFAULT    Light      on   --trigger SYSTEM

theme 'work day' ROUTINE_RESET      1:00
theme 'work day' ROUTINE_LIGHTS_ON  sunset
theme 'work day' ROUTINE_LIGHTS_OFF sunrise
theme 'day off'  ROUTINE_QUIET      23:00

room 'Living Room' Light.LIVING_ROOM  on
room Bedroom       Light.BEDROOM_LAMP on

ad_hoc define Silence          --no-reset Chromecast stop
ad_hoc define Dog              --delay 7  Chromecast stop
ad_hoc define 'All Lights On'  --no-reset Light      100
ad_hoc define 'All Lights Off' --no-reset Light      off

remote     Button.LIVING_ROOM_REMOTE 1 pushed 'All Lights On'
remote     .                         1 held   Silence

plugin 'Pair LG TV'       orc_plugins.lgtv            --function plugins.pair_tv     --section device --backend orc_plugins.lgtv.dal.tv.webos
plugin 'Test Leak Sensor' orc_plugins.yolink          --function plugins.test_sensor --section device
plugin 'Entrance Sensor'  orc_plugins.entrance_sensor
plugin Calendar           orc_plugins.calendar
plugin Travel             orc_plugins.travel         --section scene

highlight Silence 21:00 23:59

volume INFO  4
volume FATAL 10
