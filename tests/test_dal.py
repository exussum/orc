from datetime import date, datetime, timezone
from unittest.mock import patch

from orc import security
from orc.dal import net
from orc.dal.chromecast.pychromecast import _strip_googlevideo_params
from orc.dal.holiday import polygon
from orc.model import BleKey


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


class TestScanBle:
    EIK = bytes.fromhex("0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
    NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
    ANCHOR = int(NOW.timestamp()) - 5000

    def _scan(self, frames, tags):
        async def heard_over_the_air():
            return frames

        with patch.object(net, "_scan_ble", heard_over_the_air):
            return net.scan_ble(tags, self.NOW)

    def test_heard_tag_names_person(self):
        frames = {security.fmdn_eids(self.EIK, 5000)[1]}
        assert self._scan(frames, {"Alice": BleKey(self.EIK, self.ANCHOR)}) == {"Alice"}

    def test_neighbour_window_still_matches(self):
        # a slightly-off pair date lands the true window one rotation from expected
        frames = {security.fmdn_eids(self.EIK, 5000 + security.FMDN_ROTATION_SECONDS)[0]}
        assert self._scan(frames, {"Alice": BleKey(self.EIK, self.ANCHOR)}) == {"Alice"}

    def test_silence_names_nobody(self):
        assert self._scan(set(), {"Alice": BleKey(self.EIK, self.ANCHOR)}) == set()

    def test_wrong_key_names_nobody(self):
        frames = {security.fmdn_eids(self.EIK, 5000)[1]}
        assert self._scan(frames, {"Alice": BleKey(bytes(32), self.ANCHOR)}) == set()
