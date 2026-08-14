from datetime import date
from unittest.mock import patch

from orc.dal.chromecast.google_cast import _strip_googlevideo_params
from orc.dal.holiday import polygon
from orc.dal.hubitat import http as hubitat


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


class TestReboot:
    @patch("requests.post")
    def test_reboot_hits_hub_endpoint(self, post):
        hubitat.reboot()
        assert "/hub/reboot" in post.call_args[0][0]


_HOLIDAYS = [
    {"date": "2026-11-26", "exchange": "NYSE", "status": "closed"},
    {"date": "2026-11-27", "exchange": "NYSE", "status": "early-close"},
    {"date": "2026-12-25", "exchange": "NASDAQ", "status": "closed"},
]


class TestMarketHoliday:
    def _market_holiday(self, day):
        polygon._fetch_holidays.cache_clear()
        with patch("requests.get") as get:
            get.return_value.json.return_value = _HOLIDAYS
            return polygon.market_holiday(day)

    def test_nyse_closed_day_is_holiday(self):
        assert self._market_holiday(date(2026, 11, 26)) is True

    def test_early_close_day_is_work_day(self):
        assert self._market_holiday(date(2026, 11, 27)) is False

    def test_other_exchange_closure_is_work_day(self):
        assert self._market_holiday(date(2026, 12, 25)) is False

    def test_ordinary_day_is_work_day(self):
        assert self._market_holiday(date(2026, 11, 30)) is False
