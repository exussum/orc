import json
from types import SimpleNamespace

import pytest

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
