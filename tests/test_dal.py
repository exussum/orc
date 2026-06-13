from unittest.mock import MagicMock, patch

import pytest

import orc
from orc import dal
from orc import model as m
from orc.dal.chromecast import _strip_googlevideo_params
from orc.dal.sqlite import _read_light, _write_light


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

        @dal.requires_enabled("STUB")
        def fn(x):
            raise AssertionError("should not be called")

        assert fn(1) == "STUB"

    def test_disabled_calls_callable_stub_with_args(self, monkeypatch):
        monkeypatch.delenv("ORC_ENABLED", raising=False)

        @dal.requires_enabled(lambda x, y: ("stub", x, y))
        def fn(x, y):
            raise AssertionError("should not be called")

        assert fn(1, 2) == ("stub", 1, 2)

    def test_enabled_calls_through(self, monkeypatch):
        monkeypatch.setenv("ORC_ENABLED", "1")

        @dal.requires_enabled("STUB")
        def fn(x):
            return ("real", x)

        assert fn(7) == ("real", 7)


@pytest.mark.usefixtures("enabled")
class TestFetchLightState:
    def _resp(self, status, attrs=None, type="Hue Bulb"):
        resp = MagicMock()
        resp.status_code = status
        body = {"attributes": [{"name": k, "currentValue": v} for k, v in (attrs or {}).items()], "type": type}
        resp.json.return_value = body
        return resp

    @patch("requests.get")
    def test_on_with_level_returns_level(self, get):
        get.return_value = self._resp(200, {"switch": "on", "level": 50})
        assert dal.fetch_light_state(orc.Light.a) == m.Config(what=orc.Light.a, state=50)

    @patch("requests.get")
    def test_on_without_level_returns_on(self, get):
        get.return_value = self._resp(200, {"switch": "on"})
        assert dal.fetch_light_state(orc.Light.a) == m.Config(what=orc.Light.a, state="on")

    @patch("requests.get")
    def test_off_returns_off_even_with_level(self, get):
        get.return_value = self._resp(200, {"switch": "off", "level": 50})
        assert dal.fetch_light_state(orc.Light.a) == m.Config(what=orc.Light.a, state="off")

    @patch("requests.get")
    def test_non_200_returns_off(self, get):
        get.return_value = self._resp(500)
        assert dal.fetch_light_state(orc.Light.a) == m.Config(what=orc.Light.a, state="off")

    @patch("requests.get")
    def test_caches_device_type_on_first_call(self, get):
        get.return_value = self._resp(200, {"switch": "on"}, type="Hue Bulb")
        dal.fetch_light_state(orc.Light.a)
        assert _read_light(orc.Light.a)[0] == "Hue Bulb"

    @patch("requests.get")
    def test_db_truth_type_returns_off_without_trusting_hubitat(self, get):
        get.return_value = self._resp(200, {"switch": "on", "power": 0}, type="Generic Zigbee Outlet")
        assert dal.fetch_light_state(orc.Light.a) == m.Config(what=orc.Light.a, state="off")


@pytest.mark.usefixtures("enabled")
class TestDbTruthLight:
    @patch("requests.get")
    def test_cached_truth_type_returns_stored_state(self, get):
        get.return_value = MagicMock(
            status_code=200, json=lambda: {"attributes": [{"name": "switch", "currentValue": "off"}], "type": "Generic Zigbee Outlet"}
        )
        _write_light(orc.Light.a, type="Generic Zigbee Outlet", state="on")
        assert dal.fetch_light_state(orc.Light.a) == m.Config(what=orc.Light.a, state="on")

    @patch("requests.get")
    def test_cached_truth_type_no_row_returns_off(self, get):
        get.return_value = MagicMock(
            status_code=200, json=lambda: {"attributes": [{"name": "switch", "currentValue": "on"}], "type": "Generic Zigbee Outlet"}
        )
        _write_light(orc.Light.a, type="Generic Zigbee Outlet")
        assert dal.fetch_light_state(orc.Light.a) == m.Config(what=orc.Light.a, state="off")

    @patch("requests.get")
    def test_update_light_writes_state_for_truth_type(self, get):
        _write_light(orc.Light.a, type="Generic Zigbee Outlet")
        dal.update_light(orc.Light.a, on=True)
        assert _read_light(orc.Light.a)[1] == "on"

    @patch("requests.get")
    def test_update_light_does_not_write_for_reliable_type(self, get):
        _write_light(orc.Light.a, type="Hue Bulb")
        dal.update_light(orc.Light.a, on=True)
        assert _read_light(orc.Light.a)[1] is None

    @patch("requests.get")
    def test_update_light_looks_up_type_when_uncached(self, get):
        get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"attributes": [], "type": "Generic Zigbee Outlet"},
        )
        dal.update_light(orc.Light.a, on=True)
        assert _read_light(orc.Light.a) == ("Generic Zigbee Outlet", "on")

    @patch("requests.get")
    def test_update_light_brightness_writes_level_for_truth_type(self, get):
        _write_light(orc.Light.a, type="Generic Zigbee Outlet")
        dal.update_light(orc.Light.a, brightness=42)
        assert _read_light(orc.Light.a)[1] == "42"
