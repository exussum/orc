#!/usr/bin/env python3
"""List YoLink devices. Requires ORC_YOLINK_UAID and ORC_YOLINK_SECRET_KEY in env."""

import os
import sys

import requests

AUTH_URL = "https://api.yosmart.com/open/yolink/token"
API_URL = "https://api.yosmart.com/open/yolink/v2/api"


def main():
    uaid = os.environ.get("ORC_YOLINK_UAID")
    secret = os.environ.get("ORC_YOLINK_SECRET_KEY")
    if not (uaid and secret):
        print("Set ORC_YOLINK_UAID and ORC_YOLINK_SECRET_KEY in env.", file=sys.stderr)
        sys.exit(1)

    auth = requests.post(
        AUTH_URL,
        data={"grant_type": "client_credentials", "client_id": uaid, "client_secret": secret},
        timeout=10,
    )
    auth.raise_for_status()
    token = auth.json()["access_token"]

    resp = requests.post(
        API_URL,
        json={"method": "Home.getDeviceList"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()
    devices = resp.json()["data"]["devices"]

    print(f"{'Type':<20} {'Name':<30} Device ID")
    print(f"{'-' * 20} {'-' * 30} {'-' * 16}")
    for d in devices:
        print(f"{d.get('type', ''):<20} {d.get('name', ''):<30} {d.get('deviceId', '')}")


if __name__ == "__main__":
    main()
