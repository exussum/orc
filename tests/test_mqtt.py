import json
from types import SimpleNamespace

import pytest

import orc
from orc.collections import LockedDict
from orc.dal import mqtt


def _msg(topic, payload):
    return SimpleNamespace(topic=topic, payload=json.dumps(payload).encode() if isinstance(payload, dict) else payload)


def _doc(id=17, name="entrance bulb 1", attributes=None):
    attrs = attributes if attributes is not None else {"switch": "off", "level": "20"}
    return {
        "id": id,
        "name": name,
        "lastActivity": "2026-07-28T23:25:41+0000",
        "attributes": [{"name": k, "value": v, "dataType": "ENUM", "unit": None} for k, v in attrs.items()],
    }


HUB = "05bd449a-6f6d-45a6-b2e6-7ecb91105f7e"


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(mqtt, "_devices", LockedDict())
    monkeypatch.setattr(mqtt, "_hub_id", None)
    monkeypatch.setattr(mqtt, "_listeners", [])


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("ORC_ENABLED", "1")


def _receive(docs):
    """Deliver device documents through _on_message as if the broker pushed them."""
    for doc in docs:
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/{doc['id']}", doc))


class TestOnMessage:
    def test_device_document_is_cached(self):
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/17", _doc()))
        (device,) = mqtt.snapshot()
        assert (device.id, device.name) == (17, "entrance bulb 1")
        assert device.attributes == {"switch": "off", "level": "20"}
        assert device.last_activity == "2026-07-28T23:25:41+0000"

    def test_hub_id_captured_from_topic(self):
        assert mqtt.hub_id() is None
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/17", _doc()))
        assert mqtt.hub_id() == HUB

    def test_non_device_topics_ignored(self):
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/location", {"id": 1, "name": "home"}))
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/variables", {"variables": []}))
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/17/commands/on", b""))
        assert mqtt.snapshot() == []

    def test_bad_payload_ignored(self):
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/17", b"not json"))
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/17", {"unexpected": "shape"}))
        assert mqtt.snapshot() == []

    def test_update_replaces_device(self):
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/17", _doc()))
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/17", _doc(attributes={"switch": "on", "level": "80"})))
        (device,) = mqtt.snapshot()
        assert device.attributes == {"switch": "on", "level": "80"}

    def test_snapshot_sorted_by_id(self):
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/54", _doc(id=54, name="kitchen overhead")))
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/1", _doc(id=1, name="office floor lamp")))
        assert [d.id for d in mqtt.snapshot()] == [1, 54]


class TestStateRows:
    def test_rows_shape(self):
        mqtt._on_message(None, None, _msg(f"hubitat/{HUB}/devices/17", _doc()))
        (row,) = mqtt.state_rows()
        assert row["name"] == "entrance bulb 1"
        assert row["id"] == 17
        assert row["attributes"] == "level=20, switch=off"


@pytest.mark.usefixtures("enabled")
class TestFetchLightStates:
    def _state_of(self, configs, light):
        return next(c for c in configs.items if c.what is light).state

    def test_on_with_level_returns_int_level(self):
        _receive([_doc(id=1, attributes={"switch": "on", "level": "50"})])
        assert self._state_of(mqtt.fetch_light_states((orc.Light.a,)), orc.Light.a) == 50

    def test_on_without_level_returns_on(self):
        _receive([_doc(id=1, attributes={"switch": "on"})])
        assert self._state_of(mqtt.fetch_light_states((orc.Light.a,)), orc.Light.a) == "on"

    def test_off_returns_off_even_with_level(self):
        _receive([_doc(id=1, attributes={"switch": "off", "level": "50"})])
        assert self._state_of(mqtt.fetch_light_states((orc.Light.a,)), orc.Light.a) == "off"

    def test_device_missing_from_export_returns_off(self):
        _receive([_doc(id=1, attributes={"switch": "on"})])
        assert self._state_of(mqtt.fetch_light_states((orc.Light.a, orc.Light.b)), orc.Light.b) == "off"

    def test_empty_cache_returns_all_off(self):
        configs = mqtt.fetch_light_states((orc.Light.a, orc.Light.b))
        assert all(c.state == "off" for c in configs.items)

    def test_returns_only_requested_subset(self):
        _receive([_doc(id=light.value, attributes={"switch": "on"}) for light in (orc.Light.a, orc.Light.b, orc.Light.c)])
        configs = mqtt.fetch_light_states((orc.Light.a, orc.Light.c))
        assert tuple(c.what for c in configs.items) == (orc.Light.a, orc.Light.c)


class TestListeners:
    def test_fires_per_attribute_including_unchanged(self):
        events = []
        mqtt.add_listener(lambda d, a, old, new: events.append((d.id, a, old, new)))
        _receive([_doc(id=56, name="balcony door", attributes={"contact": "closed", "battery": "100"})])
        _receive([_doc(id=56, name="balcony door", attributes={"contact": "open", "battery": "100"})])
        assert (56, "contact", None, "closed") in events  # retained flood: old is None
        assert (56, "contact", "closed", "open") in events
        assert (56, "battery", "100", "100") in events  # republished unchanged, still delivered

    def test_failing_listener_does_not_break_cache_or_others(self):
        events = []
        mqtt.add_listener(lambda d, a, old, new: 1 / 0)
        mqtt.add_listener(lambda d, a, old, new: events.append(a))
        _receive([_doc(id=56, name="balcony door", attributes={"contact": "closed"})])
        assert events == ["contact"]
        assert mqtt.snapshot()[0].attributes == {"contact": "closed"}

    def test_add_listener_is_idempotent(self):
        events = []

        def listener(d, a, old, new):
            events.append(a)

        mqtt.add_listener(listener)
        mqtt.add_listener(listener)
        _receive([_doc(id=56, name="balcony door", attributes={"contact": "closed"})])
        assert events == ["contact"]


@pytest.mark.usefixtures("enabled")
class TestPublishLight:
    @pytest.fixture(autouse=True)
    def commanding_client(self, monkeypatch):
        self.published = []
        client = SimpleNamespace(publish=lambda topic, payload=None: self.published.append((topic, payload)))
        monkeypatch.setattr(mqtt, "_client", client)
        monkeypatch.setattr(mqtt, "_hub_id", HUB)

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
