from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
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


class TestBleListener:
    EIK = bytes.fromhex("0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
    NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
    ANCHOR = int(NOW.timestamp()) - 5000

    def _hear(self, frames, tags):
        with patch.object(net.BleListener, "_now", return_value=self.NOW):
            listener = net.BleListener(tags, timezone.utc)
            listener._index = net._eid_index(tags, self.NOW)
            for frame in frames:
                listener._seen(None, SimpleNamespace(service_data={net.FMDN_SERVICE_UUID: frame}))
        return listener

    def test_heard_tag_names_person(self):
        frame = bytes([0x40]) + security.fmdn_eids(self.EIK, 5000)[1]
        listener = self._hear([frame], {"Alice": BleKey(self.EIK, self.ANCHOR)})
        assert listener.present(self.NOW) == {"Alice"}

    def test_neighbour_window_still_matches(self):
        # a slightly-off pair date lands the true window one rotation from expected
        frame = bytes([0x40]) + security.fmdn_eids(self.EIK, 5000 + security.FMDN_ROTATION_SECONDS)[0]
        listener = self._hear([frame], {"Alice": BleKey(self.EIK, self.ANCHOR)})
        assert listener.present(self.NOW) == {"Alice"}

    def test_silence_names_nobody(self):
        listener = self._hear([], {"Alice": BleKey(self.EIK, self.ANCHOR)})
        assert listener.present(self.NOW) == set()

    def test_wrong_key_names_nobody(self):
        frame = bytes([0x40]) + security.fmdn_eids(self.EIK, 5000)[1]
        listener = self._hear([frame], {"Alice": BleKey(bytes(32), self.ANCHOR)})
        assert listener.present(self.NOW) == set()

    def test_delete_presence_clears_the_hearing(self):
        frame = bytes([0x40]) + security.fmdn_eids(self.EIK, 5000)[1]
        listener = self._hear([frame], {"Alice": BleKey(self.EIK, self.ANCHOR)})
        listener.delete_presence(["Alice"])
        assert listener.present(self.NOW) == set()

    def test_old_hearing_expires(self):
        frame = bytes([0x40]) + security.fmdn_eids(self.EIK, 5000)[1]
        listener = self._hear([frame], {"Alice": BleKey(self.EIK, self.ANCHOR)})
        assert listener.present(self.NOW + timedelta(seconds=net._BLE_FRESH_SECONDS + 1)) == set()
