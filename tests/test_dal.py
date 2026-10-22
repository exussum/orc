import json
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from command_cfg import ConfigError
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pykeepass import create_database
from pywebpush import WebPushException

from orc import config, security
from orc import model as m
from orc.dal import net, push, sqlite
from orc.dal.chromecast.pychromecast import _strip_googlevideo_params
from orc.dal.findhub import nova
from orc.dal.holiday import polygon
from orc.dal.push import webpush
from orc.dal.secrets import keepass
from orc.model import BleKey, Query

TRIGGER = Query("test")
VAPID_PRIVATE_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAE"
VAPID_PUBLIC_KEY = "BGsX0fLhLEJH-Lzm5WOkQPJ3A32BLeszoPShOUXYmMKWT-NC4v4af5uO5-tKfA-eFivOM1drMV7Oy7ZAaDe_UfU"
SUBSCRIPTION = m.PushSubscription("https://push.example/a", "public-key", "auth-secret")


_HOLIDAYS = [
    {"date": "2026-11-26", "exchange": "NYSE", "status": "closed"},
    {"date": "2026-11-27", "exchange": "NYSE", "status": "early-close"},
    {"date": "2026-12-25", "exchange": "NASDAQ", "status": "closed"},
]


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


class TestWebPush:
    @pytest.fixture(autouse=True)
    def vapid_secret(self):
        with patch.object(config, "secrets", m.Secrets(vapid_private_key=VAPID_PRIVATE_KEY)):
            yield

    def test_the_public_key_is_the_secrets_vapid_point(self):
        assert webpush.public_key() == VAPID_PUBLIC_KEY

    @pytest.mark.parametrize("status", [401, 403, 404, 410])
    def test_a_gone_response_raises_gone(self, status):
        response = SimpleNamespace(status_code=status)
        with patch("orc.dal.push.webpush.webpush", side_effect=WebPushException("gone", response=response)):
            with pytest.raises(push.Gone):
                webpush.send(SUBSCRIPTION, "[Plugin 01/05]", "hi", m.Notification(("leak",)))

    def test_the_payload_carries_the_link_as_given(self):
        with patch("orc.dal.push.webpush.webpush") as send:
            webpush.send(SUBSCRIPTION, "[Calendar 01/05]", "standup", m.Notification(("calendar",), "https://meet.google.com/abc?x=1"))
        assert json.loads(send.call_args.kwargs["data"])["url"] == "https://meet.google.com/abc?x=1"


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


class TestFindHub:
    OWNER_KEY = bytes(range(32))
    EIK = bytes(range(100, 132))
    PAIR_DATE = 1758575479

    def _devices_list(self, *devices):
        return b"".join(_field(2, _device(*d)) for d in devices)

    def test_reads_a_cbc_wrapped_key(self):
        iv = bytes(16)
        encryptor = Cipher(algorithms.AES(self.OWNER_KEY), modes.CBC(iv)).encryptor()
        encrypted = iv + encryptor.update(self.EIK) + encryptor.finalize()
        reply = self._devices_list(("Galaxy S22", b"x" * 48, 1, ""), ("Chipolo POP", encrypted, self.PAIR_DATE, ""))
        assert nova.registration("Chipolo POP", reply, self.OWNER_KEY) == m.TagKey(self.EIK, self.PAIR_DATE)

    def test_reads_a_gcm_wrapped_key_and_flips_an_mcu_tracker(self):
        nonce = bytes(12)
        encrypted = nonce + AESGCM(self.OWNER_KEY).encrypt(nonce, self.EIK, None)
        flipped = bytes(b ^ 0xFF for b in encrypted)
        reply = self._devices_list(("ESP32", flipped, self.PAIR_DATE, "003200"))
        assert nova.registration("ESP32", reply, self.OWNER_KEY) == m.TagKey(self.EIK, self.PAIR_DATE)

    def test_unknown_device_is_a_lookup_error(self):
        with pytest.raises(LookupError, match="no Find Hub device named 'Chipolo POP'"):
            nova.registration("Chipolo POP", self._devices_list(("Galaxy S22", b"x" * 48, 1, "")), self.OWNER_KEY)

    def test_identity_key_authenticates_then_posts(self):
        secrets = m.Secrets(find_hub_username="u", find_hub_aas_token="t", find_hub_android_id="a", find_hub_owner_key=self.OWNER_KEY.hex())
        iv = bytes(16)
        encryptor = Cipher(algorithms.AES(self.OWNER_KEY), modes.CBC(iv)).encryptor()
        reply = self._devices_list(("Chipolo POP", iv + encryptor.update(self.EIK) + encryptor.finalize(), self.PAIR_DATE, ""))
        with patch.object(nova.gpsoauth, "perform_oauth", return_value={"Auth": "bearer"}) as oauth, patch("requests.post") as post:
            post.return_value.content = reply
            assert nova.identity_key("Chipolo POP", secrets, 5) == m.TagKey(self.EIK, self.PAIR_DATE)
        assert oauth.call_args.args[:3] == ("u", "t", "a")
        assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer bearer"

    def test_refused_token_is_a_permission_error(self):
        with patch.object(nova.gpsoauth, "perform_oauth", return_value={"Error": "BadAuthentication"}):
            with pytest.raises(PermissionError, match="BadAuthentication"):
                nova.identity_key("Chipolo POP", m.Secrets(), 5)


def _varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _field(number, value):
    if isinstance(value, int):
        return _varint(number << 3) + _varint(value)
    return _varint(number << 3 | 2) + _varint(len(value)) + value


def _device(name, encrypted_key, pair_date, model_id):
    registration = _field(19, _field(1, encrypted_key)) + _field(23, pair_date)
    if model_id:
        registration += _field(21, model_id.encode())
    return _field(5, name.encode()) + _field(4, _field(1, registration))


def test_durations_reach_the_table_one_batch_at_a_time():
    sqlite._durations.clear()
    sqlite._pending.clear()
    for _ in range(sqlite._DURATION_BATCH - 1):
        sqlite.update_avg("x", 1.0)
    assert sqlite.durations() == {"x": (sqlite._DURATION_BATCH - 1, 1.0)}
    sqlite.load_durations()
    assert sqlite.durations() == {}
    for _ in range(sqlite._DURATION_BATCH):
        sqlite.update_avg("x", 1.0)
    sqlite.load_durations()
    assert sqlite.durations() == {"x": (sqlite._DURATION_BATCH, 1.0)}


@pytest.mark.parametrize(
    "offsets, expected",
    [([100], 100), ([100, 400], 100), ([100, 700], 700), ([100, 700, 300], 700), ([100, 700, 100], 100)],
)
def test_tag_clock_row_moves_only_by_more_than_half_a_window(offsets, expected):
    for offset in offsets:
        sqlite.upsert_tag_clock("Alice", m.TagClock(offset, 0))
    assert sqlite.fetch_tag_clocks() == {"Alice": m.TagClock(expected, 0)}


class TestPresence:
    EIK = bytes.fromhex("0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
    NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
    ANCHOR = int(NOW.timestamp()) - 5000
    KEY = BleKey(lambda: TestPresence.EIK, ANCHOR)

    ADDRESS = "11:22:33:44:55:66"

    def _hear(self, frames, tags):
        with patch.object(net.Presence, "_now", return_value=self.NOW):
            presence = net.Presence()
            presence._tags = tags
            presence._index = net._eid_index(tags, {}, self.NOW)
            for frame in frames:
                presence._seen(SimpleNamespace(address=self.ADDRESS), SimpleNamespace(service_data={net.FMDN_SERVICE_UUID: frame}))
        return presence

    def _present(self, presence):
        return presence.present(self.NOW - timedelta(hours=1))

    def _heard(self):
        frame = bytes([0x40]) + security.fmdn_eids(self.EIK, 5000)[1]
        return self._hear([frame], {"Alice": self.KEY})

    def test_heard_tag_names_person(self):
        presence = self._heard()
        assert self._present(presence) == {"Alice"}

    def test_neighbour_window_still_matches(self):
        # a slightly-off pair date lands the true window one rotation from expected
        frame = bytes([0x40]) + security.fmdn_eids(self.EIK, 5000 + security.FMDN_ROTATION_SECONDS)[0]
        presence = self._hear([frame], {"Alice": self.KEY})
        assert self._present(presence) == {"Alice"}

    def test_far_window_matches_and_narrows_the_index(self):
        far = 5000 + 6 * security.FMDN_ROTATION_SECONDS
        frame = bytes([0x40]) + security.fmdn_eids(self.EIK, far)[1]
        tags = {"Alice": self.KEY}
        presence = self._hear([frame], tags)
        assert self._present(presence) == {"Alice"}
        narrowed = net._eid_index(tags, presence._clocks.copy(), self.NOW)
        assert len(narrowed) == 9
        assert set(security.fmdn_eids(self.EIK, far)) <= narrowed.keys()

    def test_stale_clock_widens_the_index_again(self):
        tags = {"Alice": self.KEY}
        clocks = {"Alice": m.TagClock(0, int(self.NOW.timestamp()) - net._BLE_CLOCK_TRUST_SECONDS)}
        assert len(net._eid_index(tags, clocks, self.NOW)) == 3 * (2 * net._BLE_SEARCH_WINDOWS + 1)

    def test_a_rotation_reports_the_exact_clock(self):
        second = bytes([0x40]) + security.fmdn_eids(self.EIK, 5000 + security.FMDN_ROTATION_SECONDS)[1]
        presence = self._heard()
        presence._on_clock = MagicMock()
        with patch.object(net.Presence, "_now", return_value=self.NOW):
            presence._seen(SimpleNamespace(address=self.ADDRESS), SimpleNamespace(service_data={net.FMDN_SERVICE_UUID: second}))
        presence._on_clock.assert_called_once_with("Alice", m.TagClock(5120 - 5000, int(self.NOW.timestamp()), second[1:]))

    def test_a_seeded_clock_starts_narrow(self):
        tags = {"Alice": self.KEY}
        clocks = {"Alice": m.TagClock(0, int(self.NOW.timestamp()))}
        assert len(net._eid_index(tags, clocks, self.NOW)) == 9

    def test_silence_names_nobody(self):
        presence = self._hear([], {"Alice": self.KEY})
        assert self._present(presence) == set()

    def test_wrong_key_names_nobody(self):
        frame = bytes([0x40]) + security.fmdn_eids(self.EIK, 5000)[1]
        presence = self._hear([frame], {"Alice": BleKey(lambda: bytes(32), self.ANCHOR)})
        assert self._present(presence) == set()

    def test_forget_clears_the_hearing(self):
        presence = self._heard()
        presence.forget(["Alice"], TRIGGER)
        assert self._present(presence) == set()

    def test_hearing_outside_the_window_expires(self):
        presence = self._heard()
        assert presence.present(self.NOW + timedelta(seconds=1)) == set()

    def test_pause_hides_prior_hearing_until_resume(self):
        presence = self._heard()
        presence.pause(self.NOW)
        assert self._present(presence) == set()
        presence.resume(TRIGGER)
        assert self._present(presence) == {"Alice"}

    def _forgotten(self):
        """Heard once, so the tag's address is known, then forgotten: only a probe can mark it again."""
        presence = self._heard()
        presence.forget(["Alice"], TRIGGER)
        return presence

    def _probe(self, presence, client):
        with patch.object(net, "BleakClient", client), patch.object(net.Presence, "_now", return_value=self.NOW):
            presence.probe(["Alice"], TRIGGER)

    def test_probe_marks_reachable_tag(self):
        presence = self._forgotten()
        connections = []

        @asynccontextmanager
        async def client(address, timeout):
            connections.append(address)
            yield

        self._probe(presence, client)
        assert connections == [self.ADDRESS]
        assert self._present(presence) == {"Alice"}

    def test_probe_failure_marks_nothing(self):
        presence = self._forgotten()

        def client(address, timeout):
            raise TimeoutError

        self._probe(presence, client)
        assert self._present(presence) == set()

    def test_probe_without_known_address_skips(self):
        presence = net.Presence()
        client = MagicMock()
        self._probe(presence, client)
        client.assert_not_called()


def test_missed_job_is_logged(caplog):
    from datetime import datetime

    from apscheduler.events import EVENT_JOB_MISSED, JobExecutionEvent

    from orc.dal import scheduler

    scheduler._log_missed(JobExecutionEvent(EVENT_JOB_MISSED, "iot-Sunrise", "memory", datetime(2026, 1, 1, tzinfo=config.settings.tz)))
    (record,) = [r for r in caplog.records if r.levelname == "ERROR"]
    assert "iot-Sunrise" in record.getMessage()


class TestKeepass:
    @pytest.fixture(scope="session")
    def keepass_db(self, tmp_path_factory):
        path = tmp_path_factory.mktemp("keepass")
        kp = create_database(path / "secrets.kdbx", password="open sesame")
        kp.add_entry(kp.root_group, "MARKET_HOLIDAYS_URL", "", "https://m")
        kp.add_entry(kp.root_group, "FEED_URL", "", "https://x")
        # argon2 at the file's default cost is 0.19s per open; these tests only need the format
        params = kp.kdbx.header.value.dynamic_header.kdf_parameters.data.dict
        params["I"].value, params["M"].value, params["P"].value = 1, 1 << 14, 1
        kp.save()
        return str(path)

    @pytest.fixture(autouse=True)
    def config_dir(self, keepass_db):
        with patch.object(config, "config_dir", keepass_db):
            yield

    def test_the_typed_password_opens_the_database(self, keepass_db):
        with patch("orc.dal.secrets.keepass.getpass", return_value="open sesame"):
            assert keepass.fetch_secrets() == m.Secrets(market_holidays_url="https://m", other={"FEED_URL": "https://x"})

    def test_a_wrong_password_is_a_config_error(self, keepass_db):
        with patch("orc.dal.secrets.keepass.getpass", return_value="nope"):
            with pytest.raises(ConfigError):
                keepass.fetch_secrets()
