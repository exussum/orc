from unittest.mock import patch

import pytest

import orc
from orc.dal import hubitat
from orc.dal.chromecast import _strip_googlevideo_params
from orc.dal.decorators import requires_enabled


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
class TestUpdateLight:
    @patch("requests.Session.get")
    def test_on_hits_on_endpoint(self, get):
        hubitat.update_light(orc.Light.a, on=True)
        assert "/devices/1/on" in get.call_args[0][0]

    @patch("requests.Session.get")
    def test_brightness_uses_set_level(self, get):
        hubitat.update_light(orc.Light.a, brightness=42)
        assert "/devices/1/setLevel/42" in get.call_args[0][0]

    @patch("requests.Session.get")
    def test_brightness_without_capability_raises(self, get):
        with pytest.raises(ValueError):
            hubitat.update_light(orc.Light.b, brightness=42)
        get.assert_not_called()
