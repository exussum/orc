setting base_url         http://orc.internal.example
setting lan_domain       orc.internal.example
setting jobs_db          sqlite:////tmp/jobs.sqlite
setting lat              42.4440
setting long             -76.5019
setting broadlink_codes  /etc/orc/broadlink_codes.json
setting mqtt_host        hubitat.example
setting warning_device   Chromecast.LIVING_ROOM
setting attention_device USB.Speakers
setting emergency_device Chromecast.BEDROOM
setting emergency_routine ROUTINE_EMERGENCY

provider secrets    orc.dal.secrets.stub
provider weather    orc.dal.weather.stub
provider holiday    orc.dal.holiday.stub
provider chromecast orc.dal.chromecast.stub
provider blaster    orc.dal.blaster.stub
provider hubitat    orc.dal.hubitat.stub
provider audio      orc.dal.audio.stub
provider push       orc.dal.push.stub
provider findhub    orc.dal.findhub.stub

device define Light --sort 0
device add Light BEDROOM_LAMP 'bedroom lamp'     --room Bedroom
device add Light LIVING_ROOM  'living room desk'
device add Light KITCHEN      kitchen
device add Light HALL         hall
device add Light PORCH        porch
device add Light OFFICE       office
device seal Light

device define USB
device add USB Speakers Speakers --room Office
device seal USB

device define Chromecast --sort 1
device add Chromecast LIVING_ROOM 'Living room mini' --room Living
device add Chromecast BEDROOM     'Bedroom mini'     --room Bedroom
device seal Chromecast

device only Button LIVING_ROOM_REMOTE scene --room Living
device only AC LIVING clip-1 --name 'Living room AC' --sort 2

device define Sensor
device add Sensor ENTRANCE_SENSOR 'front door motion sensor'
device add Sensor PATIO_DOOR      'balcony door'
device seal Sensor

routine define ROUTINE_RESET      Reset
routine append ROUTINE_RESET      Light      off  --trigger-person SYSTEM
routine append .                  AC         off  --trigger-person SYSTEM

routine define ROUTINE_LIGHTS_ON  'Lights On'
routine append ROUTINE_LIGHTS_ON  Light      on   --trigger-person SYSTEM

routine define ROUTINE_LIGHTS_OFF 'Lights Off'
routine append ROUTINE_LIGHTS_OFF Light      off  --trigger-person SYSTEM

routine define ROUTINE_QUIET      Quiet
routine append ROUTINE_QUIET      Chromecast stop --trigger-person SYSTEM

routine define ROUTINE_DEFAULT    Welcome
routine append ROUTINE_DEFAULT    Light      on   --trigger-person SYSTEM
routine append .                  AC         cool:low:75 --trigger-person SYSTEM


routine define ROUTINE_EMERGENCY 'Emergency'
routine append ROUTINE_EMERGENCY Chromecast.BEDROOM 100

theme 'work day' ROUTINE_RESET      1:00
theme 'work day' ROUTINE_LIGHTS_ON  sunset
theme 'work day' ROUTINE_LIGHTS_OFF sunrise
theme 'day off'  ROUTINE_QUIET      23:00

room 'Living Room' Light.LIVING_ROOM  on
room Bedroom       Light.BEDROOM_LAMP on

ad_hoc define AD_HOC_SILENCE        Silence          --section scene --no-reset Chromecast stop
ad_hoc define AD_HOC_DOG            Dog              --section scene --delay 7  Chromecast stop
ad_hoc define AD_HOC_ALL_LIGHTS_ON  'All Lights On'  --section scene --no-reset Light      100
ad_hoc define AD_HOC_ALL_LIGHTS_OFF 'All Lights Off' --section scene --no-reset Light      off

remote     Button.LIVING_ROOM_REMOTE 1 pushed AD_HOC_ALL_LIGHTS_ON
remote     .                         1 held   AD_HOC_SILENCE


group media 'Media'

button AD_HOC_SILENCE media --stripe-from 21:00 --stripe-to 23:59

person Alice alice-phone aa:bb:cc:dd:ee:01
person Rex   rex-collar  aa:bb:cc:dd:ee:02
