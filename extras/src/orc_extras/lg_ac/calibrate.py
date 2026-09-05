"""Interactive field-map calibration for a new AC model.

Connects to the local broker as an extra subscriber, watches the device's
``device_packet`` frames, and walks you through each mode / fan speed / the
temperature range. Writes ``fieldmap/<model>.json`` at the end. Run it while the
main proxy is running and the AC is provisioned:

    .venv/bin/python scripts/calibrate.py
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import paho.mqtt.client as mqtt

from orc_extras.lg_ac import api

# Standard clip TLV ids — the field *ids* are consistent across models; only the
# value codes (which this script calibrates) differ.
FIELDS = {
    "power": 0x1F7,
    "mode": 0x1F9,
    "fan_mode": 0x1FA,
    "current_temperature": 0x1FD,
    "temperature": 0x1FE,
}
TEMP_DIVISOR = 2  # clip encodes temperature as °C × 2

_MESSAGE_PREFIX = "clip/message/devices/"
_PROVISIONING_PREFIX = "clip/provisioning/devices/"

_lock = threading.Lock()
_raw: dict[int, int] = {}  # latest merged TLV values from the device
_detected_model: str | None = None


def _on_message(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
    global _detected_model
    try:
        payload = json.loads(msg.payload.rstrip(b"\x00"))
    except ValueError:
        return
    if msg.topic.startswith(_PROVISIONING_PREFIX) and payload.get("kind"):
        _detected_model = payload["kind"]
    elif msg.topic.startswith(_MESSAGE_PREFIX) and payload.get("cmd") == "device_packet":
        pkt = api.frame_tlv(bytes.fromhex(payload.get("data", "")))
        if pkt is None:
            return
        with _lock:
            _raw.update({f.type_id: f.value for f in pkt.fields})


def _read(field_id: int) -> int | None:
    with _lock:
        return _raw.get(field_id)


def _capture_code(field_id: int, label: str) -> int:
    while True:
        input(f"  Set the AC to {label}, then press Enter... ")
        time.sleep(0.7)  # let the device's push arrive
        code = _read(field_id)
        if code is None:
            print("  No frame seen yet — change it on the AC, then press Enter again.")
            continue
        answer = input(f"  Read {label} = {code}. Correct? [Y/n] ").strip().lower()
        if answer in ("", "y", "yes"):
            return code


def _capture_temp(label: str) -> int:
    while True:
        input(f"  Press Up/Down to the {label} temperature, then press Enter... ")
        time.sleep(0.7)
        raw = _read(FIELDS["temperature"])
        if raw is None:
            print("  No frame seen yet — change the temperature, then press Enter again.")
            continue
        answer = input(f"  Read {label} raw = {raw} (≈{raw / TEMP_DIVISOR}°C). Correct? [Y/n] ").strip().lower()
        if answer in ("", "y", "yes"):
            return raw


def _ask_list(prompt: str) -> list[str]:
    while True:
        raw = input(prompt).strip()
        names = [n.strip() for n in raw.split(",") if n.strip()]
        if names:
            return names
        print("  Enter at least one, comma-separated.")


def main() -> None:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="lg_ac-calibrate")
    client.on_message = _on_message
    client.connect("127.0.0.1", 1883, keepalive=30)
    client.loop_start()
    client.subscribe("#", qos=0)

    print("Connected. Waiting for the AC to send a frame (change something on it if nothing happens)...")
    while not _raw:
        time.sleep(0.5)

    model = input(f"Model name [{_detected_model or ''}]: ").strip() or _detected_model
    if not model:
        print("A model name is required. Aborting.")
        return

    print("\n--- Modes ---")
    mode_names = _ask_list("Which modes does your AC have? (e.g. cool,dry,fan_only,econ): ")
    modes = {name: _capture_code(FIELDS["mode"], name) for name in mode_names}

    print("\n--- Fan speeds ---")
    fan_names = _ask_list("Which fan speeds does your AC have? (e.g. low,med,high): ")
    fans = {name: _capture_code(FIELDS["fan_mode"], name) for name in fan_names}

    print("\n--- Temperature range (sweep low → high) ---")
    min_raw = _capture_temp("LOWEST")
    max_raw = _capture_temp("HIGHEST")

    def _celsius(raw: int) -> float | int:
        value = raw / TEMP_DIVISOR
        return int(value) if value.is_integer() else value

    data = {
        "fields": {name: hex(fid) for name, fid in FIELDS.items()},
        "modes": modes,
        "fans": fans,
        "temperature": {"divisor": TEMP_DIVISOR, "min": _celsius(min_raw), "max": _celsius(max_raw)},
    }

    path = api.save_fieldmap(model, data)
    client.loop_stop()
    print(f"\nWrote {path}")
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
