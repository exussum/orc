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


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("ORC_ENABLED", "1")


class FakeClient:
    """Stands in for a paho client: loop_start replays the given docs through the
    on_message callback fetch_light_states installed, like retained delivery would."""

    def __init__(self, docs):
        self._docs = docs

    def connect(self, host, port, keepalive):
        pass

    def loop_start(self):
        for doc in self._docs:
            self.on_message(self, None, _msg(f"hubitat/{HUB}/devices/{doc['id']}", doc))

    def loop_stop(self):
        pass

    def disconnect(self):
        pass


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
    def _fetch(self, monkeypatch, docs, lights, timeout=1.0):
        monkeypatch.setattr(mqtt, "new_client", lambda: (FakeClient(docs), "host", 1883))
        return mqtt.fetch_light_states(lights, timeout=timeout)

    def _state_of(self, configs, light):
        return next(c for c in configs.items if c.what is light).state

    def test_on_with_level_returns_int_level(self, monkeypatch):
        docs = [_doc(id=1, attributes={"switch": "on", "level": "50"})]
        configs = self._fetch(monkeypatch, docs, (orc.Light.a,))
        assert self._state_of(configs, orc.Light.a) == 50

    def test_on_without_level_returns_on(self, monkeypatch):
        docs = [_doc(id=1, attributes={"switch": "on"})]
        configs = self._fetch(monkeypatch, docs, (orc.Light.a,))
        assert self._state_of(configs, orc.Light.a) == "on"

    def test_off_returns_off_even_with_level(self, monkeypatch):
        docs = [_doc(id=1, attributes={"switch": "off", "level": "50"})]
        configs = self._fetch(monkeypatch, docs, (orc.Light.a,))
        assert self._state_of(configs, orc.Light.a) == "off"

    def test_device_missing_from_export_returns_off(self, monkeypatch):
        docs = [_doc(id=1, attributes={"switch": "on"})]
        configs = self._fetch(monkeypatch, docs, (orc.Light.a, orc.Light.b), timeout=0.01)
        assert self._state_of(configs, orc.Light.b) == "off"

    def test_unconfigured_broker_returns_all_off(self, monkeypatch):
        monkeypatch.setattr(mqtt, "new_client", lambda: None)
        configs = mqtt.fetch_light_states((orc.Light.a, orc.Light.b))
        assert all(c.state == "off" for c in configs.items)

    def test_returns_only_requested_subset(self, monkeypatch):
        docs = [_doc(id=light.value, attributes={"switch": "on"}) for light in (orc.Light.a, orc.Light.b, orc.Light.c)]
        configs = self._fetch(monkeypatch, docs, (orc.Light.a, orc.Light.c))
        assert tuple(c.what for c in configs.items) == (orc.Light.a, orc.Light.c)
