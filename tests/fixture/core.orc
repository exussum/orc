setting base_url         http://orc.test
setting lan_domain       orc.test
setting jobs_db          sqlite:////tmp/jobs.sqlite
setting lat              40.7143
setting long             -74.0060
setting broadlink_codes  /etc/orc/codes.json
setting mqtt_host        hub.test
setting warning_device   Chromecast.CC
setting attention_device USB.AUDIO
setting emergency_device USB.AUDIO
setting emergency_routine ROUTINE_EMERGENCY

device define Light
device add Light LAMP h1 --room Bedroom
device add Light DESK h2
device seal Light

device define USB
device add USB AUDIO 'USB Audio'
device seal USB

device only Chromecast CC host3 --room Living

routine define ROUTINE_RESET   Reset
routine append ROUTINE_RESET   Light         off --trigger SYSTEM
routine append .               Chromecast.CC stop
routine define ROUTINE_DEFAULT Welcome
routine append ROUTINE_DEFAULT Light         on
routine define ROUTINE_MEETING Meeting --skip-replay

theme 'work day' ROUTINE_RESET   1:00
theme 'day off'  ROUTINE_DEFAULT sunset

room Bedroom Light.LAMP on

device only Button REMOTE scene

person Spence host9 aa:bb
routine append ROUTINE_DEFAULT Chromecast.CC stop --trigger Spence

ad_hoc define ROUTINE_EMERGENCY --no-reset USB.AUDIO 100
ad_hoc define Silence           --no-reset Chromecast.CC stop
ad_hoc define Dog               --delay 7  Chromecast.CC stop
ad_hoc define 'All Lights Off'  --no-reset Light off
ad_hoc append 'All Lights Off' Chromecast.CC stop

remote     Button.REMOTE 1 pushed 'All Lights Off'
remote     .             1 held   Silence

highlight Silence 21:00 23:59

plugin 'Test Light' orc.plugins light_test --section device --icon tv
