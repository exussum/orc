"""Static assertion that every provider backend satisfies its interface.

mypy checks these assignments; the block never runs, so nothing is imported at
runtime. A backend drifting from its Protocol (a renamed or missing method, a
changed signature) fails type-checking here rather than at load time.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orc.dal import interfaces
    from orc.dal.blaster import broadlink
    from orc.dal.blaster import stub as blaster_stub
    from orc.dal.chromecast import google_cast
    from orc.dal.chromecast import stub as chromecast_stub
    from orc.dal.holiday import polygon
    from orc.dal.holiday import stub as holiday_stub
    from orc.dal.hubitat import http as hubitat_http
    from orc.dal.hubitat import stub as hubitat_stub
    from orc.dal.mqtt import hubitat
    from orc.dal.mqtt import stub as mqtt_stub
    from orc.dal.secrets import bws
    from orc.dal.secrets import stub as secrets_stub
    from orc.dal.weather import open_meteo
    from orc.dal.weather import stub as weather_stub

    _secrets_real: interfaces.SecretsService = bws
    _secrets_stub: interfaces.SecretsService = secrets_stub
    _weather_real: interfaces.WeatherService = open_meteo
    _weather_stub: interfaces.WeatherService = weather_stub
    _holiday_real: interfaces.HolidayService = polygon
    _holiday_stub: interfaces.HolidayService = holiday_stub
    _mqtt_real: interfaces.MqttService = hubitat
    _mqtt_stub: interfaces.MqttService = mqtt_stub
    _chromecast_real: interfaces.ChromecastService = google_cast
    _chromecast_stub: interfaces.ChromecastService = chromecast_stub
    _blaster_real: interfaces.BlasterService = broadlink
    _blaster_stub: interfaces.BlasterService = blaster_stub
    _hubitat_real: interfaces.HubitatService = hubitat_http
    _hubitat_stub: interfaces.HubitatService = hubitat_stub
