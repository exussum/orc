from unittest.mock import MagicMock, patch

import pytest

import orc
from orc import model as m
from orc.dal import hubitat
from orc.dal._decorators import requires_enabled
from orc.dal.chromecast import _strip_googlevideo_params
from orc.dal.sqlite import read_light, write_light


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("ORC_ENABLED", "1")


class TestStripGoogleVideoParams:
    def test_keeps_id_drops_other_params(self):
        url = "https://r1---sn-abc.googlevideo.com/videoplayback?id=abc123&sig=xxx&ip=1.2.3.4"
        assert _strip_googlevideo_params(url) == "https://r1---sn-abc.googlevideo.com/videoplayback?id=abc123"

    def test_no_id_clears_query(self):
        url = "https://r1.googlevideo.com/videoplayback?sig=xxx&ip=1.2.3.4"
        assert _strip_googlevideo_params(url) == "https://r1.googlevideo.com/videoplayback"

    def test_non_googlevideo_unchanged(self):
        url = "https://example.com/audio?id=abc&token=xyz"
        assert _strip_googlevideo_params(url) == url

    def test_hostname_must_end_with_googlevideo(self):
        url = "https://googlevideo.com.evil.example/x?id=abc&sig=xxx"
        assert _strip_googlevideo_params(url) == url

    def test_no_hostname(self):
        assert _strip_googlevideo_params("not a url") == "not a url"


class TestRequiresEnabled:
    def test_disabled_returns_static_stub(self, monkeypatch):
        monkeypatch.delenv("ORC_ENABLED", raising=False)

        @requires_enabled("STUB")
        def fn(x):
            raise AssertionError("should not be called")

        assert fn(1) == "STUB"

    def test_disabled_calls_callable_stub_with_args(self, monkeypatch):
        monkeypatch.delenv("ORC_ENABLED", raising=False)

        @requires_enabled(lambda x, y: ("stub", x, y))
        def fn(x, y):
            raise AssertionError("should not be called")

        assert fn(1, 2) == ("stub", 1, 2)

    def test_enabled_calls_through(self, monkeypatch):
        monkeypatch.setenv("ORC_ENABLED", "1")

        @requires_enabled("STUB")
        def fn(x):
            return ("real", x)

        assert fn(7) == ("real", 7)


@pytest.mark.usefixtures("enabled")
class TestFetchLightStates:
    def _resp(self, status, devices=()):
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = list(devices)
        return resp

    def _device(self, light, attrs=None, type="Hue Bulb"):
        return {
            "id": str(light.value),
            "type": type,
            "attributes": dict(attrs or {}),
        }

    def _state_of(self, configs, light):
        return next(c for c in configs.items if c.what is light).state

    @patch("requests.get")
    def test_on_with_level_returns_level(self, get):
        get.return_value = self._resp(200, [self._device(orc.Light.a, {"switch": "on", "level": 50})])
        assert self._state_of(hubitat.fetch_light_states((orc.Light.a,)), orc.Light.a) == 50

    @patch("requests.get")
    def test_on_with_string_level_returns_int(self, get):
        get.return_value = self._resp(200, [self._device(orc.Light.a, {"switch": "on", "level": "50"})])
        assert self._state_of(hubitat.fetch_light_states((orc.Light.a,)), orc.Light.a) == 50

    @patch("requests.get")
    def test_on_without_level_returns_on(self, get):
        get.return_value = self._resp(200, [self._device(orc.Light.a, {"switch": "on"})])
        assert self._state_of(hubitat.fetch_light_states((orc.Light.a,)), orc.Light.a) == "on"

    @patch("requests.get")
    def test_off_returns_off_even_with_level(self, get):
        get.return_value = self._resp(200, [self._device(orc.Light.a, {"switch": "off", "level": 50})])
        assert self._state_of(hubitat.fetch_light_states((orc.Light.a,)), orc.Light.a) == "off"

    @patch("requests.get")
    def test_returns_only_requested_subset(self, get):
        get.return_value = self._resp(
            200,
            [
                self._device(orc.Light.a, {"switch": "on"}),
                self._device(orc.Light.b, {"switch": "on"}),
                self._device(orc.Light.c, {"switch": "on"}),
            ],
        )
        configs = hubitat.fetch_light_states((orc.Light.a, orc.Light.c))
        assert tuple(c.what for c in configs.items) == (orc.Light.a, orc.Light.c)

    @patch("requests.get")
    def test_db_truth_type_returns_off_without_trusting_hubitat(self, get):
        get.return_value = self._resp(200, [self._device(orc.Light.a, {"switch": "on", "power": 0}, type="Generic Zigbee Outlet")])
        assert self._state_of(hubitat.fetch_light_states((orc.Light.a,)), orc.Light.a) == "off"


@pytest.mark.usefixtures("enabled")
class TestDbTruthLight:
    @patch("requests.get")
    def test_cached_truth_type_returns_stored_state(self, get):
        get.return_value = MagicMock(
            status_code=200,
            json=lambda: [{"id": "1", "attributes": {"switch": "off"}, "type": "Generic Zigbee Outlet"}],
        )
        write_light(orc.Light.a, type="Generic Zigbee Outlet", state="on")
        configs = hubitat.fetch_light_states((orc.Light.a,))
        assert configs.items[0] == m.Config(what=orc.Light.a, state="on")

    @patch("requests.get")
    def test_cached_truth_type_no_row_returns_off(self, get):
        get.return_value = MagicMock(
            status_code=200,
            json=lambda: [{"id": "1", "attributes": {"switch": "on"}, "type": "Generic Zigbee Outlet"}],
        )
        write_light(orc.Light.a, type="Generic Zigbee Outlet")
        configs = hubitat.fetch_light_states((orc.Light.a,))
        assert configs.items[0] == m.Config(what=orc.Light.a, state="off")

    @patch("requests.get")
    def test_update_light_writes_state_for_truth_type(self, get):
        get.return_value = MagicMock(json=lambda: {"type": "Generic Zigbee Outlet"})
        hubitat.update_light(orc.Light.a, on=True)
        assert read_light(orc.Light.a) == ("Generic Zigbee Outlet", "on")

    @patch("requests.get")
    def test_update_light_does_not_write_for_reliable_type(self, get):
        get.return_value = MagicMock(json=lambda: {"type": "Hue Bulb"})
        hubitat.update_light(orc.Light.a, on=True)
        assert read_light(orc.Light.a) == (None, None)

    @patch("requests.get")
    def test_update_light_brightness_writes_level_for_truth_type(self, get):
        get.return_value = MagicMock(json=lambda: {"type": "Generic Zigbee Outlet"})
        hubitat.update_light(orc.Light.a, brightness=42)
        assert read_light(orc.Light.a) == ("Generic Zigbee Outlet", "42")
