import json
from datetime import UTC, datetime
from types import SimpleNamespace

import paho.mqtt.client as paho
import pytest

import orc
from orc import model as m
from orc.collections import LockedDict
from orc.dal.mqtt import hubitat, none
from orc.dal.mqtt import paho as mqtt

HUB = "05bd449a-6f6d-45a6-b2e6-7ecb91105f7e"


@pytest.fixture(autouse=True)
def reset_mqtt(monkeypatch):
    for name in ("_devices", "_command_sent", "_commanded"):
        monkeypatch.setattr(hubitat, name, LockedDict())
    monkeypatch.setattr(hubitat, "hub_id", None)
    monkeypatch.setattr(mqtt, "_client", None)
    mqtt.register(hubitat)


@pytest.fixture
def mqtt_events():
    return []


@pytest.fixture
def mqtt_client(monkeypatch):
    client = SimpleNamespace(published=[], subscribe=lambda *a, **k: None)
    client.publish = lambda topic, payload=None, retain=False: client.published.append((topic, payload, retain)) or SimpleNamespace(rc=0)
    monkeypatch.setattr(mqtt, "_client", client)
    hubitat.hub_id = HUB
    return client


class TestOnMessage:
    def test_device_document_is_cached(self):
        doc = _doc()
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/17", doc))
        (state,) = mqtt.snapshot()
        assert state.device == m.Device("17", "entrance bulb 1", "hubitat")
        assert state.attributes == {"switch": "off", "level": "20"}
        assert state.last_activity == doc["lastActivity"]

    def test_hub_id_captured_from_topic(self):
        assert hubitat.hub_id is None
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/17", _doc()))
        assert hubitat.hub_id == HUB

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
        monkeypatch.setattr(hubitat, "_document", lambda topic, doc: 1 / 0)
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
    def test_fires_per_attribute_including_unchanged(self, mqtt_events):
        mqtt.add_status_listener(lambda s: mqtt_events.append((s.device.id, s.attribute, s.old, s.new)))
        _seen(56, name="balcony door", contact="closed", battery="100")
        assert mqtt_events == []
        _receive([_doc(id=56, name="balcony door", attributes={"contact": "open", "battery": "100"})])
        assert ("56", "contact", "closed", "open") in mqtt_events
        assert ("56", "battery", "100", "100") in mqtt_events  # republished unchanged, still delivered

    def test_failing_listener_does_not_break_cache_or_others(self, mqtt_events):
        mqtt.add_status_listener(lambda s: 1 / 0)
        mqtt.add_status_listener(lambda s: mqtt_events.append(s.attribute))
        _seen(56, name="balcony door", contact="closed")
        _receive([_doc(id=56, name="balcony door", attributes={"contact": "open"})])
        assert mqtt_events == ["contact"]
        assert mqtt.snapshot()[0].attributes == {"contact": "open"}

    def test_replayed_document_updates_cache_without_events(self, mqtt_events):
        mqtt.add_status_listener(lambda s: mqtt_events.append(s.attribute))
        doc = _doc(id=56, name="balcony door", attributes={"contact": "open"})
        _receive([doc, doc])  # first sighting, then a replay (reconnect flood, hub republish)
        assert mqtt_events == []
        assert mqtt.snapshot()[0].attributes == {"contact": "open"}

    def test_document_differing_only_in_last_activity_fires(self, mqtt_events):
        mqtt.add_status_listener(lambda s: mqtt_events.append((s.attribute, s.old, s.new)))
        _seen(56, name="balcony door", contact="open")
        _receive([_doc(id=56, name="balcony door", attributes={"contact": "open"}, last_activity="2026-07-29T00:00:05+0000")])
        assert mqtt_events == [("contact", "open", "open")]


class TestButtonEvents:
    def test_fires_listener_as_a_change_with_no_before(self, mqtt_events):
        mqtt.add_status_listener(lambda s: mqtt_events.append((s.device.id, s.device.name, s.attribute, s.old, s.new)))
        _seen(10, name="remote", pushed="1")
        mqtt._on_message(None, None, _button_msg("held"))
        assert mqtt_events == [("10", "remote", "held", None, 1)]

    def test_press_from_a_device_the_hub_never_exported_is_dropped(self, mqtt_events):
        mqtt.add_status_listener(lambda *a: mqtt_events.append(a))
        mqtt._on_message(None, None, _button_msg("held"))
        assert mqtt_events == []

    def test_clearing_publish_ignored(self, mqtt_events):
        mqtt.add_status_listener(lambda *a: mqtt_events.append(a))
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/10/button/1", b"", retain=False))
        assert mqtt_events == []

    def test_command_echo_ignored(self, mqtt_events):
        mqtt.add_status_listener(lambda *a: mqtt_events.append(a))
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/10/commands/release", b"1", retain=False))
        assert mqtt_events == []

    def test_bad_payload_ignored(self, mqtt_events):
        mqtt.add_status_listener(lambda *a: mqtt_events.append(a))
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/10/button/1", b"not json", retain=False))
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/10/button/1", {"unexpected": "shape"}, retain=False))
        assert mqtt_events == []

    def test_failing_listener_does_not_break_others(self, mqtt_events):
        mqtt.add_status_listener(lambda s: 1 / 0)
        mqtt.add_status_listener(lambda s: mqtt_events.append(s.attribute))
        _seen(10, name="remote", pushed="1")
        mqtt._on_message(None, None, _button_msg("pushed"))
        assert mqtt_events == ["pushed"]

    def test_button_event_does_not_touch_device_cache(self):
        mqtt._on_message(None, None, _button_msg("pushed"))
        assert mqtt.snapshot() == []


class TestRouting:
    def test_namespace_picks_the_adapter_and_parses_json(self):
        adapter = _Adapter()
        mqtt.register(adapter)
        mqtt._on_message(None, None, _msg("clip/message/devices/abc", {"cmd": "x"}, retain=False))
        mqtt._on_message(None, None, _msg("clip/message/devices/abc", b'{"cmd":"y"}\x00', retain=False))
        mqtt._on_message(None, None, _msg("clip/echo", b"42", retain=False))
        mqtt._on_message(None, None, _msg("other/topic", b"{}", retain=False))
        assert adapter.seen == [("clip/message/devices/abc", {"cmd": "x"}), ("clip/message/devices/abc", {"cmd": "y"}), ("clip/echo", {})]

    def test_a_adapter_publishes_in_its_own_namespace_only(self, mqtt_client):
        adapter = _Adapter()
        adapter.replies = [m.Message("clip/reply", {"ok": 1}, retain=True)]
        mqtt.register(adapter)
        mqtt._on_message(None, None, _msg("clip/in", b"{}", retain=False))
        assert mqtt_client.published == [("clip/reply", '{"ok": 1}', True)]
        with pytest.raises(ValueError):
            adapter.publish(m.Message("hubitat/x", None))

    def test_register_refuses_a_taken_namespace_or_device_type(self):
        mqtt.register(_Adapter())
        with pytest.raises(ValueError):
            mqtt.register(_Adapter())
        with pytest.raises(ValueError):
            mqtt.register(SimpleNamespace(namespaces=("lime",), device_types=("Light",)))

    def test_connect_subscribes_every_namespace(self):
        mqtt.register(_Adapter())
        subscribed = []
        mqtt._on_connect(mqtt._adapters, SimpleNamespace(subscribe=lambda topic, qos: subscribed.append(topic)), None, None, 0)
        assert subscribed == ["hubitat/#", "clip/#"]

    def test_start_sends_each_adapter_hello(self, monkeypatch, mqtt_client):
        monkeypatch.setattr(mqtt, "_new_client", lambda *a: mqtt_client)
        adapter = _Adapter()
        adapter.hello = [m.Message("clip/hello", {"q": 1}, retain=True)]
        mqtt.register(adapter)
        mqtt.start()
        assert mqtt_client.published == [("clip/hello", '{"q": 1}', True)]

    @pytest.fixture
    def no_broker(self, monkeypatch):
        monkeypatch.setattr(mqtt, "_new_client", lambda *a: pytest.fail("client built"))

    def test_start_builds_no_client_without_a_namespace(self, monkeypatch, no_broker):
        monkeypatch.setattr(mqtt, "_adapters", [none])
        mqtt.start()
        assert mqtt._client is None

    def test_discover_skips_the_broker_without_a_namespace(self, no_broker):
        assert mqtt.discover(none, m.Secrets()) == {}

    def test_command_routes_by_device_type(self, mqtt_client):
        mqtt.register(_Adapter())
        mqtt.command(orc.AC.unit, "cool")
        assert mqtt_client.published == [("clip/command/clip-1", '{"set": "cool"}', False)]
        with pytest.raises(LookupError):
            mqtt.command(orc.Chromecast.x, 10)

    def test_command_raises_when_the_broker_is_away(self, mqtt_client):
        mqtt_client.publish = lambda *a, **k: SimpleNamespace(rc=paho.MQTT_ERR_NO_CONN)
        with pytest.raises(ConnectionError, match="not currently connected"):
            mqtt.command(orc.Light.a, m.ON)


class TestPublishLight:
    @pytest.fixture(autouse=True)
    def published(self, mqtt_client):
        self.published = mqtt_client.published

    def test_on_publishes_on_command(self):
        mqtt.command(orc.Light.a, m.ON)
        assert self.published == [(f"hubitat/{HUB}/devices/1/commands/on", None, False)]

    def test_off_publishes_off_command(self):
        mqtt.command(orc.Light.a, m.OFF)
        assert self.published == [(f"hubitat/{HUB}/devices/1/commands/off", None, False)]

    def test_brightness_publishes_set_level_with_raw_payload(self):
        mqtt.command(orc.Light.a, 42)
        assert self.published == [(f"hubitat/{HUB}/devices/1/commands/setLevel", "42", False)]

    def test_brightness_zero_without_capability_publishes_off(self):
        mqtt.command(orc.Light.b, 0)
        assert self.published == [(f"hubitat/{HUB}/devices/2/commands/off", None, False)]

    def test_brightness_without_capability_raises(self):
        with pytest.raises(ValueError):
            mqtt.command(orc.Light.b, 42)
        assert self.published == []

    def test_unstarted_client_raises(self, monkeypatch):
        monkeypatch.setattr(mqtt, "_client", None)
        with pytest.raises(RuntimeError):
            mqtt.command(orc.Light.a, m.ON)


@pytest.mark.usefixtures("mqtt_client")
class TestStatusSource:
    def sources(self, **attributes):
        return {s.attribute: s.source for s in hubitat.decode(f"hubitat/{HUB}/devices/1", _doc(id=1, attributes=attributes))}

    def test_a_commanded_switch_is_orc(self):
        _seen(1, switch="off", level="20")
        mqtt.command(orc.Light.a, m.ON)
        assert self.sources(switch="on", level="20")["switch"] is m.Source.ORC

    def test_unmoved_attributes_are_the_device(self):
        _seen(1, switch="off", level="20", battery="90")
        mqtt.command(orc.Light.a, m.ON)
        sources = self.sources(switch="on", level="20", battery="90")
        assert sources["level"] is sources["battery"] is hubitat.HubitatSource.HUBITAT

    def test_an_uncommanded_move_is_external(self):
        _seen(1, switch="off", level="20")
        assert self.sources(switch="on", level="20")["switch"] is m.Source.EXTERNAL


@pytest.mark.usefixtures("mqtt_client")
class TestExternalChanges:
    @pytest.fixture(autouse=True)
    def external_statuses(self):
        self.external_statuses = []
        mqtt.add_status_listener(lambda s: s.source is m.Source.EXTERNAL and self.external_statuses.append((s.attribute, s.old, s.new)))

    def test_a_commanded_change_is_not_external(self):
        mqtt.command(orc.Light.a, 42)
        _seen(1, switch="off", level="20")
        _receive([_doc(id=1, attributes={"switch": "on", "level": "43"})])  # drivers round through 0-254, one off is a match
        assert self.external_statuses == []

    def test_a_value_nobody_asked_for_is_external(self):
        mqtt.command(orc.Light.a, 42)
        _seen(1, switch="on", level="20")
        _receive([_doc(id=1, attributes={"switch": "on", "level": "80"})])
        assert self.external_statuses == [("level", "20", "80")]

    def test_an_unchanged_attribute_is_never_external(self):
        _seen(1, switch="on", level="20")
        _receive([_doc(id=1, attributes={"switch": "on", "level": "20"}, last_activity="2026-07-29T00:00:05+0000")])
        assert self.external_statuses == []

    def test_a_matching_document_consumes_the_command(self):
        mqtt.command(orc.Light.a, m.ON)
        _seen(1, switch="off")
        _receive([_doc(id=1, attributes={"switch": "on"})])
        _receive([_doc(id=1, attributes={"switch": "off"})])
        _receive([_doc(id=1, attributes={"switch": "on"})])
        assert self.external_statuses == [("switch", "on", "off"), ("switch", "off", "on")]


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
        return mqtt.discover(hubitat, secrets or m.Secrets(mqtt_user="u", mqtt_password="p"), timeout=timeout)

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


class _Adapter:
    namespaces = ("clip",)
    device_types = ("AC",)

    def __init__(self):
        self.seen, self.replies, self.hello = [], [], []

    def attach(self, publish):
        self.publish = publish

    def decode(self, topic, doc):
        self.seen.append((topic, doc))
        for reply in self.replies:
            self.publish(reply)
        return ()

    def encode(self, device, value):
        return (m.Message(f"clip/command/{device.value}", {"set": value}),)

    def snapshot(self):
        return ()

    def start(self):
        for message in self.hello:
            self.publish(message)


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


def _receive(docs):
    """Deliver device documents through _on_message as if the broker pushed them."""
    for doc in docs:
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/{doc['id']}", doc))


def _seen(id, name="entrance bulb 1", **attributes):
    """A device's first document only caches it: nothing fires until the next one."""
    _receive([_doc(id=id, name=name, attributes=attributes)])


def _button_msg(event_type, device_id=10, button=1):
    payload = {"event_type": event_type, "button": button, "timestamp": "2026-07-29T22:42:37+0000"}
    return _msg(f"hubitat/{HUB}/devices/{device_id}/button/{button}", payload, retain=False)
