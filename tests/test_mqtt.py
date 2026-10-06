import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import orc
from orc import model as m
from orc.dal.mqtt import hubitat as mqtt


def _msg(topic, payload, retain=True):
    return SimpleNamespace(topic=topic, payload=json.dumps(payload).encode() if isinstance(payload, dict) else payload, retain=retain)


def _doc(id=17, name="entrance bulb 1", attributes=None, last_activity=None):
    attrs = attributes if attributes is not None else {"switch": "off", "level": "20"}
    return {
        "id": id,
        "name": name,
        # fresh by default: stale documents update the cache but fire no listeners
        "lastActivity": last_activity or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S%z"),
        "attributes": [{"name": k, "value": v, "dataType": "ENUM", "unit": None} for k, v in attrs.items()],
    }


HUB = "05bd449a-6f6d-45a6-b2e6-7ecb91105f7e"


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(mqtt, "_codec", mqtt.Hubitat())
    monkeypatch.setattr(mqtt, "_listeners", [])


def _receive(docs):
    """Deliver device documents through _on_message as if the broker pushed them."""
    for doc in docs:
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/{doc['id']}", doc))


class TestOnMessage:
    def test_device_document_is_cached(self):
        doc = _doc()
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/17", doc))
        (state,) = mqtt.snapshot()
        assert state.device == m.Device("17", "entrance bulb 1", "hubitat")
        assert state.attributes == {"switch": "off", "level": "20"}
        assert state.last_activity == doc["lastActivity"]

    def test_hub_id_captured_from_topic(self):
        assert mqtt._codec.hub_id is None
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/17", _doc()))
        assert mqtt._codec.hub_id == HUB

    def test_non_device_topics_ignored(self):
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/location", {"id": 1, "name": "home"}))
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/variables", {"variables": []}))
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/17/commands/on", b""))
        assert mqtt.snapshot() == []

    def test_bad_payload_ignored(self):
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/17", b"not json"))
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/17", {"unexpected": "shape"}))
        assert mqtt.snapshot() == []

    def test_handler_failure_is_logged_not_raised(self, monkeypatch, caplog):
        monkeypatch.setattr(mqtt._codec, "_document", lambda topic, doc: 1 / 0)
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/17", _doc()))
        (record,) = [r for r in caplog.records if r.levelname == "ERROR"]
        assert record.exc_info[0] is ZeroDivisionError

    def test_update_replaces_device(self):
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/17", _doc()))
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/17", _doc(attributes={"switch": "on", "level": "80"})))
        (device,) = mqtt.snapshot()
        assert device.attributes == {"switch": "on", "level": "80"}

    def test_snapshot_sorted_by_id(self):
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/54", _doc(id=54, name="kitchen overhead")))
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/1", _doc(id=1, name="office floor lamp")))
        assert [d.device.id for d in mqtt.snapshot()] == ["1", "54"]


class TestListeners:
    def test_fires_per_attribute_including_unchanged(self):
        events = []
        mqtt.add_listener(lambda d, a, old, new: events.append((d.id, a, old, new)))
        _receive([_doc(id=56, name="balcony door", attributes={"contact": "closed", "battery": "100"})])
        assert events == []  # first sighting (retained flood): state only, no events
        _receive([_doc(id=56, name="balcony door", attributes={"contact": "open", "battery": "100"})])
        assert ("56", "contact", "closed", "open") in events
        assert ("56", "battery", "100", "100") in events  # republished unchanged, still delivered

    def test_failing_listener_does_not_break_cache_or_others(self):
        events = []
        mqtt.add_listener(lambda d, a, old, new: 1 / 0)
        mqtt.add_listener(lambda d, a, old, new: events.append(a))
        _receive([_doc(id=56, name="balcony door", attributes={"contact": "closed"})])
        _receive([_doc(id=56, name="balcony door", attributes={"contact": "open"})])
        assert events == ["contact"]
        assert mqtt.snapshot()[0].attributes == {"contact": "open"}

    def test_replayed_document_updates_cache_without_events(self):
        events = []
        mqtt.add_listener(lambda d, a, old, new: events.append(a))
        doc = _doc(id=56, name="balcony door", attributes={"contact": "open"})
        _receive([doc, doc])  # first sighting, then a replay (reconnect flood, hub republish)
        assert events == []
        assert mqtt.snapshot()[0].attributes == {"contact": "open"}

    def test_document_differing_only_in_last_activity_fires(self):
        events = []
        mqtt.add_listener(lambda d, a, old, new: events.append((a, old, new)))
        _receive([_doc(id=56, name="balcony door", attributes={"contact": "open"}, last_activity="2026-07-29T00:00:00+0000")])
        _receive([_doc(id=56, name="balcony door", attributes={"contact": "open"}, last_activity="2026-07-29T00:00:05+0000")])
        assert events == [("contact", "open", "open")]


def _button_msg(event_type, device_id=10, button=1):
    payload = {"event_type": event_type, "button": button, "timestamp": "2026-07-29T22:42:37+0000"}
    return _msg(f"hubitat/{HUB}/devices/{device_id}/button/{button}", payload, retain=False)


class TestButtonEvents:
    def test_fires_listener_as_a_change_with_no_before(self):
        events = []
        mqtt.add_listener(lambda d, a, old, new: events.append((d.id, d.name, a, old, new)))
        _receive([_doc(id=10, name="remote", attributes={"pushed": "1"})])
        mqtt._on_message(None, None, _button_msg("held"))
        assert events == [("10", "remote", "held", None, 1)]

    def test_press_from_a_device_the_hub_never_exported_is_dropped(self):
        events = []
        mqtt.add_listener(lambda *a: events.append(a))
        mqtt._on_message(None, None, _button_msg("held"))
        assert events == []

    def test_clearing_publish_ignored(self):
        events = []
        mqtt.add_listener(lambda *a: events.append(a))
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/10/button/1", b"", retain=False))
        assert events == []

    def test_command_echo_ignored(self):
        events = []
        mqtt.add_listener(lambda *a: events.append(a))
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/10/commands/release", b"1", retain=False))
        assert events == []

    def test_bad_payload_ignored(self):
        events = []
        mqtt.add_listener(lambda *a: events.append(a))
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/10/button/1", b"not json", retain=False))
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/10/button/1", {"unexpected": "shape"}, retain=False))
        assert events == []

    def test_failing_listener_does_not_break_others(self):
        events = []
        mqtt.add_listener(lambda d, a, old, new: 1 / 0)
        mqtt.add_listener(lambda d, a, old, new: events.append(a))
        _receive([_doc(id=10, name="remote", attributes={"pushed": "1"})])
        mqtt._on_message(None, None, _button_msg("pushed"))
        assert events == ["pushed"]

    def test_button_event_does_not_touch_device_cache(self):
        mqtt._on_message(None, None, _button_msg("pushed"))
        assert mqtt.snapshot() == []


class TestPublishLight:
    @pytest.fixture(autouse=True)
    def commanding_client(self, monkeypatch):
        self.published = []
        client = SimpleNamespace(publish=lambda topic, payload=None, retain=False: self.published.append((topic, payload)))
        monkeypatch.setattr(mqtt, "_client", client)
        mqtt._codec.hub_id = HUB

    def test_on_publishes_on_command(self):
        mqtt.publish_light(orc.Light.a, on=True)
        assert self.published == [(f"hubitat/{HUB}/devices/1/commands/on", None)]

    def test_off_publishes_off_command(self):
        mqtt.publish_light(orc.Light.a, on=False)
        assert self.published == [(f"hubitat/{HUB}/devices/1/commands/off", None)]

    def test_brightness_publishes_set_level_with_raw_payload(self):
        mqtt.publish_light(orc.Light.a, brightness=42)
        assert self.published == [(f"hubitat/{HUB}/devices/1/commands/setLevel", "42")]

    def test_brightness_zero_without_capability_publishes_off(self):
        mqtt.publish_light(orc.Light.b, brightness=0)
        assert self.published == [(f"hubitat/{HUB}/devices/2/commands/off", None)]

    def test_brightness_without_capability_raises(self):
        with pytest.raises(ValueError):
            mqtt.publish_light(orc.Light.b, brightness=42)
        assert self.published == []

    def test_unstarted_client_raises(self, monkeypatch):
        monkeypatch.setattr(mqtt, "_client", None)
        with pytest.raises(RuntimeError):
            mqtt.publish_light(orc.Light.a, on=True)


class TestStatusSource:
    @pytest.fixture(autouse=True)
    def commanding_client(self, monkeypatch):
        monkeypatch.setattr(mqtt, "_client", SimpleNamespace(publish=lambda topic, payload=None, retain=False: None))
        mqtt._codec.hub_id = HUB

    def _sources(self, doc):
        return {s.attribute: s.source for s in mqtt._codec.decode(f"hubitat/{HUB}/devices/1", doc)}

    def test_a_commanded_switch_is_orc_and_the_rest_is_the_device(self):
        mqtt.publish_light(orc.Light.a, on=True)
        self._sources(_doc(id=1, attributes={"switch": "off", "level": "20", "battery": "90"}))
        assert self._sources(_doc(id=1, attributes={"switch": "on", "level": "20", "battery": "90"})) == {
            "switch": m.Source.ORC,
            "level": mqtt.HubitatSource.HUBITAT,
            "battery": mqtt.HubitatSource.HUBITAT,
        }

    def test_an_uncommanded_move_is_external(self):
        self._sources(_doc(id=1, attributes={"switch": "off", "level": "20"}))
        assert self._sources(_doc(id=1, attributes={"switch": "on", "level": "20"})) == {
            "switch": m.Source.EXTERNAL,
            "level": mqtt.HubitatSource.HUBITAT,
        }


class TestExternalChanges:
    @pytest.fixture(autouse=True)
    def commanding_client(self, monkeypatch):
        monkeypatch.setattr(mqtt, "_client", SimpleNamespace(publish=lambda topic, payload=None, retain=False: None))
        monkeypatch.setattr(mqtt, "_external_listeners", [])
        mqtt._codec.hub_id = HUB
        self.external = []
        mqtt.add_external_listener(lambda d, a, old, new: self.external.append((a, old, new)))

    def test_a_commanded_change_is_not_external(self):
        mqtt.publish_light(orc.Light.a, brightness=42)
        _receive([_doc(id=1, attributes={"switch": "off", "level": "20"})])
        _receive([_doc(id=1, attributes={"switch": "on", "level": "43"})])  # drivers round through 0-254, one off is a match
        assert self.external == []

    def test_a_value_nobody_asked_for_is_external(self):
        mqtt.publish_light(orc.Light.a, brightness=42)
        _receive([_doc(id=1, attributes={"switch": "off", "level": "20"})])
        _receive([_doc(id=1, attributes={"switch": "on", "level": "80"})])
        assert self.external == [("level", "20", "80")]

    def test_an_unchanged_attribute_is_never_external(self):
        _receive([_doc(id=1, attributes={"switch": "on", "level": "20"})])
        _receive([_doc(id=1, attributes={"switch": "on", "level": "20"}, last_activity="2026-07-29T00:00:05+0000")])
        assert self.external == []

    def test_a_matching_document_consumes_the_command(self):
        mqtt.publish_light(orc.Light.a, on=True)
        _receive([_doc(id=1, attributes={"switch": "off"})])
        _receive([_doc(id=1, attributes={"switch": "on"})])
        _receive([_doc(id=1, attributes={"switch": "off"})])
        _receive([_doc(id=1, attributes={"switch": "on"})])
        assert self.external == [("switch", "on", "off"), ("switch", "off", "on")]


class TestFetchHubitatConfig:
    class FakeClient:
        """Replays retained documents through the on_message callback at loop_start,
        like the broker's retained flood."""

        def __init__(self, *a, **k):
            self.docs, self.retain = [], True

        def username_pw_set(self, user, password):
            pass

        def reconnect_delay_set(self, min_delay, max_delay):
            pass

        def connect_async(self, host, port, keepalive):
            pass

        def loop_start(self):
            for doc in self.docs:
                self.on_message(self, None, _msg(f"hubitat/{HUB}/devices/{doc['id']}", doc, retain=self.retain))

        def loop_stop(self):
            pass

        def disconnect(self):
            pass

    def _fetch(self, monkeypatch, docs, timeout=1.0, secrets=None, retain=True):
        fake = self.FakeClient()
        fake.docs, fake.retain = docs, retain
        monkeypatch.setattr(mqtt.mqtt, "Client", lambda *a, **k: fake)
        return mqtt.fetch_hubitat_config(secrets or m.Secrets(mqtt_user="u", mqtt_password="p"), timeout=timeout)

    def test_maps_name_to_id_and_infers_dimmable_from_level(self, monkeypatch):
        docs = [
            _doc(id=17, name="entrance bulb 1", attributes={"switch": "off", "level": "20"}),
            _doc(id=1, name="office floor lamp", attributes={"switch": "off", "power": "0"}),
        ]
        config = self._fetch(monkeypatch, docs)
        assert config == {
            "entrance bulb 1": ("17", frozenset([m.Capability.change_level])),
            "office floor lamp": ("1", frozenset()),
        }

    def test_empty_flood_fails_boot(self, monkeypatch):
        with pytest.raises(RuntimeError, match="no device documents"):
            self._fetch(monkeypatch, [], timeout=0.1)

    def test_unretained_documents_fail_boot(self, monkeypatch):
        with pytest.raises(RuntimeError, match="not retained for entrance bulb 1"):
            self._fetch(monkeypatch, [_doc(id=17, name="entrance bulb 1")], timeout=0.1, retain=False)
