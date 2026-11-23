device define Light
device add Light A h1
device add Light B h2
device add Light C h3
device seal Light
device only Chromecast CC host3

routine define R Triggered
routine include R NIGHT --trigger-person SYSTEM
routine include R LATE_DEFINED

ad_hoc define AD_HOC_ALL Everything
ad_hoc include . NIGHT
ad_hoc append . Light.C 5

ad_hoc define AD_HOC_MINUS Minus
ad_hoc include . 'NIGHT - {Light.A}'


command define NIGHT
command append . '{Light.A, Light.B}' 1
command append . Light.C on
command append . Chromecast stop

command define LATE_DEFINED
command append . Light.A off
