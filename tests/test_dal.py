from unittest.mock import patch

from orc.dal.chromecast.google_cast import _strip_googlevideo_params
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
