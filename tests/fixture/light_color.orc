device define Light
device add Light BULB h1
device seal Light
ad_hoc define AD_HOC_WARM  Warm  Light.BULB ct:3000
ad_hoc define AD_HOC_DIM   Dim   Light.BULB ct:2700
ad_hoc define AD_HOC_AMBER Amber Light.BULB hue:8:87
ad_hoc define AD_HOC_PINK  Pink  Light.BULB hue:92:40
ad_hoc define AD_HOC_DARK  Dark  Light.BULB ct:0
