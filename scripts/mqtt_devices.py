"""Enumerate devices exposed by Hubitat's MQTT Export integration.

Connects to the broker, collects the retained per-device JSON documents from
hubitat/<hub-id>/devices/<device-id>, and prints one line per device with its
id, name, and current attribute values.

Usage:
    python scripts/mqtt_devices.py --host hub.example.org [--username u --password p] [--json]
"""

import argparse
import json
import time
from typing import Any

import paho.mqtt.client as mqtt

# device id (int) -> device document from hubitat/<hub-id>/devices/<id>
devices: dict[int, dict[str, Any]] = {}


def _on_message(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
    parts = msg.topic.split("/")
    if len(parts) != 4 or parts[0] != "hubitat" or parts[2] != "devices":
        return
    try:
        devices[int(parts[3])] = json.loads(msg.payload)
    except ValueError:
        pass


def _on_connect(client: mqtt.Client, userdata: Any, flags: Any, rc: Any, *args: Any) -> None:
    # Subscribe here, not before the loop: paho drops (does not queue)
    # subscriptions made before the CONNACK arrives.
    client.subscribe("hubitat/#", qos=0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="MQTT broker address (the hub, if using its built-in broker)")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--wait", type=float, default=3.0, help="seconds to collect retained messages")
    parser.add_argument("--json", action="store_true", help="dump raw device documents as JSON")
    args = parser.parse_args()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if args.username:
        client.username_pw_set(args.username, args.password or "")
    client.on_connect = _on_connect
    client.on_message = _on_message
    client.connect(args.host, args.port, keepalive=30)
    client.loop_start()
    time.sleep(args.wait)
    client.loop_stop()
    client.disconnect()

    if args.json:
        print(json.dumps(devices, indent=2, sort_keys=True))
        return

    print(f"{len(devices)} devices")
    for device_id in sorted(devices):
        doc = devices[device_id]
        attrs = ", ".join(f"{a['name']}={a.get('value')}" for a in doc.get("attributes", []))
        print(f"  {device_id}: {doc.get('name')} [{attrs}]")

    if not devices:
        print("\nNothing found. Check that the MQTT Export app is enabled and devices are selected in it.")


if __name__ == "__main__":
    main()
