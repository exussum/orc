setting base_url         http://orc.internal.example
setting lan_domain       orc.internal.example
setting jobs_db          sqlite:////tmp/jobs.sqlite
setting lat              40.7143
setting long             -74.0060
setting broadlink_codes  /etc/orc/broadlink_codes.json
setting mqtt_host        hubitat.example
setting warning_device   Chromecast.LIVING_ROOM
setting attention_device USB.TEST
setting emergency_device USB.TEST
setting emergency_routine ROUTINE_EMERGENCY

provider secrets    orc.dal.secrets.stub
provider weather    orc.dal.weather.stub
provider holiday    orc.dal.holiday.stub
provider chromecast orc.dal.chromecast.stub
provider blaster    orc.dal.blaster.stub
provider hubitat    orc.dal.hubitat.stub
provider broker     orc.dal.broker.stub
provider audio      orc.dal.audio.stub
provider push       orc.dal.push.stub
provider findhub    orc.dal.findhub.stub

device define Light
device add Light BEDROOM_LAMP 'bedroom lamp'     --room Bedroom
device add Light LIVING_ROOM  'living room desk'
device seal Light

device define USB
device add USB TEST 'NO_AUDIO_DEVICE_IN_TESTS'
device seal USB

device only Chromecast LIVING_ROOM        'Living room mini' --room Living
device only Button     LIVING_ROOM_REMOTE scene              --room Living
device only AC
device only LGTV
device only WebOS
device only Leak

device define Sensor
device add Sensor LIVING_ROOM 'living room sensor'
device seal Sensor

routine define ROUTINE_RESET      Reset
routine append ROUTINE_RESET      Light      off  --trigger-person SYSTEM
routine define ROUTINE_LIGHTS_ON  'Lights On'
routine append ROUTINE_LIGHTS_ON  Light      on   --trigger-person SYSTEM
routine define ROUTINE_LIGHTS_OFF 'Lights Off'
routine append ROUTINE_LIGHTS_OFF Light      off  --trigger-person SYSTEM
routine define ROUTINE_QUIET      Quiet
routine append ROUTINE_QUIET      Chromecast stop --trigger-person SYSTEM
routine define ROUTINE_DEFAULT    Welcome
routine append ROUTINE_DEFAULT    Light      on   --trigger-person SYSTEM

routine define ROUTINE_EMERGENCY Emergency
routine append ROUTINE_EMERGENCY USB.TEST 100

theme 'work day' ROUTINE_RESET      1:00
theme 'work day' ROUTINE_LIGHTS_ON  sunset
theme 'work day' ROUTINE_LIGHTS_OFF sunrise
theme 'day off'  ROUTINE_QUIET      23:00

room 'Living Room' Light.LIVING_ROOM  on
room Bedroom       Light.BEDROOM_LAMP on

person Alice alice.example aa:bb:cc:dd:ee:ff
tag    Alice 'Alice Tag'

ad_hoc define AD_HOC_SILENCE        Silence          --section scene --no-reset Chromecast stop
ad_hoc define AD_HOC_DOG            Dog              --section scene --delay 7  Chromecast stop
ad_hoc define AD_HOC_ALL_LIGHTS_OFF 'All Lights Off' --section scene --no-reset Light      off

remote     Button.LIVING_ROOM_REMOTE 1 held   AD_HOC_SILENCE

plugin Buttons            orc.plugins.buttons
plugin Battery            orc.plugins.battery
plugin 'Enable Notifications'  orc.plugins                                --section system --icon bell
plugin 'Disable Notifications' orc.plugins                                --section system --icon bell-slash
plugin 'Pair LG TV'       orc_extras.lg_tv           pair_tv     --section device --backend orc_extras.lg_tv.dal.stub
plugin 'Test Leak Sensor' orc_extras.yolink          test_sensor --section device --backend orc_extras.yolink.dal.stub
plugin 'Entrance Sensor'  orc_extras.entrance_sensor
plugin Calendar           orc_extras.calendar
plugin 'LG AC'            orc_extras.lg_ac
plugin React              orc_extras.react --section system
plugin Travel             orc_extras.travel --section scene

group media 'Media'

button AD_HOC_SILENCE media --stripe-from 21:00 --stripe-to 23:59

