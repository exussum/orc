# /// script
# requires-python = ">=3.12"
# dependencies = ["bleak", "gpsoauth", "httpx", "cryptography", "ecdsa"]
# ///
"""Refresh a Find Hub tag's identity key in Bitwarden and in a running orc.

    uv run scripts/rekey_tag.py <device name> <secret name> <orc url> [--scan] [--dry-run]

Fetches the tag's current identity key from the Find Hub account, compares it
with the named Bitwarden secret, and on a difference writes the new value and
runs orc's Refresh Secrets plugin at the given URL. The key never prints. The
account session comes from four Bitwarden secrets that GoogleFindMyTools'
Chrome login produces once: FIND_HUB_USERNAME, FIND_HUB_AAS_TOKEN,
FIND_HUB_ANDROID_ID and FIND_HUB_OWNER_KEY. A refused token or a failed
decrypt means that login has to be repeated.

--scan listens on this machine's Bluetooth radio first and refuses to write
unless the key derives at least one FMDN frame heard within ±200 rotation
windows of the pair date; neighbours' tags are heard too, and the tag has to
be in range.
"""

import asyncio
import json
import subprocess
import sys
import time
from uuid import uuid4

import gpsoauth
import httpx
from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from ecdsa.curves import SECP160r1

NOVA_URL = "https://android.googleapis.com/nova/nbe_list_devices"
ADM_SCOPE = "oauth2:https://www.googleapis.com/auth/android_device_manager"
ADM_APP = "com.google.android.apps.adm"
ADM_SIGNATURE = "38918a453d07199354f8b19af05ec6562ced5788"
MCU_MODEL_ID = "003200"
ROTATION = 1024
SEARCH_WINDOWS = 200
SCAN_SECONDS = 15
FMDN_SERVICE_UUID = "0000feaa-0000-1000-8000-00805f9b34fb"
FMDN_FRAME_TYPES = (0x40, 0x41)
REFRESH_PATH = "/api/run/Refresh%20Secrets"

type Field = tuple[int, int | bytes]


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if len(args) < 3:
        print(__doc__)
        return 2
    device, secret_name, orc_url = args[0], args[1], args[2].rstrip("/")
    dry_run = "--dry-run" in argv
    frames = scan_frames() if "--scan" in argv else []
    if "--scan" in argv and not frames:
        print(f"no FMDN frames heard in {SCAN_SECONDS}s")
        return 1
    for frame in frames:
        print(f"  {frame.hex()}")

    vault = {s["key"]: s for s in bws("list")}
    creds = {k: vault[f"FIND_HUB_{k}"]["value"] for k in ("USERNAME", "AAS_TOKEN", "ANDROID_ID", "OWNER_KEY")}
    eik, pair_date = fetch_identity_key(device, creds)
    if frames and not derives_any(eik, pair_date, frames):
        print("fetched key derives none of the frames heard; not writing")
        return 1

    current = vault[secret_name]
    if current["value"].lower() == eik.hex():
        print("unchanged")
        return 0
    print(f"{secret_name} differs from the account's key (pair date {pair_date})")
    if dry_run:
        return 0
    bws("edit", "--value", eik.hex(), current["id"])
    httpx.get(orc_url + REFRESH_PATH, timeout=30).raise_for_status()
    print("re-keyed and refreshed")
    return 0


def fetch_identity_key(device: str, creds: dict[str, str]) -> tuple[bytes, int]:
    auth = gpsoauth.perform_oauth(
        creds["USERNAME"], creds["AAS_TOKEN"], creds["ANDROID_ID"], service=ADM_SCOPE, app=ADM_APP, client_sig=ADM_SIGNATURE
    )
    if "Auth" not in auth:
        raise SystemExit(f"Find Hub token refused ({auth.get('Error', 'no error given')}): repeat the Chrome login")
    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Authorization": f"Bearer {auth['Auth']}",
        "Accept-Language": "en-US",
        "User-Agent": "fmd/20006320; gzip",
    }
    reply = httpx.post(NOVA_URL, content=list_request(), headers=headers, timeout=30)
    reply.raise_for_status()
    for metadata in submessages(reply.content, 2):
        name = first(metadata, 5)
        if not isinstance(name, bytes) or name.decode() != device:
            continue
        information = first(metadata, 4)
        registration = first(parse(instance(information)), 1)
        fields = parse(instance(registration))
        secrets = first(fields, 19)
        encrypted = first(parse(instance(secrets)), 1)
        pair_date = first(fields, 23)
        model_id = first(fields, 21)
        if not isinstance(encrypted, bytes) or not isinstance(pair_date, int):
            raise SystemExit(f"{device!r} has no identity key registration")
        if isinstance(model_id, bytes) and model_id.decode() == MCU_MODEL_ID:
            encrypted = bytes(b ^ 0xFF for b in encrypted)
        return decrypt_identity_key(bytes.fromhex(creds["OWNER_KEY"]), encrypted), pair_date
    raise SystemExit(f"no Find Hub device named {device!r}")


def list_request() -> bytes:
    request_id = uuid4().hex.encode()
    payload = b"\x08\x02" + b"\x1a" + bytes([len(request_id)]) + request_id
    return b"\x0a" + bytes([len(payload)]) + payload


def decrypt_identity_key(owner_key: bytes, encrypted: bytes) -> bytes:
    if len(encrypted) == 48:
        decryptor = Cipher(algorithms.AES(owner_key), modes.CBC(encrypted[:16])).decryptor()
        return decryptor.update(encrypted[16:]) + decryptor.finalize()
    elif len(encrypted) == 60:
        return AESGCM(owner_key).decrypt(encrypted[:12], encrypted[12:], None)
    raise SystemExit(f"unexpected identity key length {len(encrypted)}")


def scan_frames() -> list[bytes]:
    frames: set[bytes] = set()

    def seen(device: BLEDevice, data: AdvertisementData) -> None:
        frame = data.service_data.get(FMDN_SERVICE_UUID)
        if frame and len(frame) == 22 and frame[0] in FMDN_FRAME_TYPES:
            frames.add(bytes(frame))

    async def run() -> None:
        async with BleakScanner(seen):
            await asyncio.sleep(SCAN_SECONDS)

    print(f"scanning for {SCAN_SECONDS}s")
    asyncio.run(run())
    return sorted(frames)


def derives_any(eik: bytes, pair_date: int, frames: list[bytes]) -> bool:
    targets = {f[1:21] for f in frames}
    expected = int(time.time()) - pair_date
    for step in range(-SEARCH_WINDOWS, SEARCH_WINDOWS + 1):
        window = (expected + step * ROTATION) & ~(ROTATION - 1)
        if window >= 0 and eid(eik, window) in targets:
            return True
    return False


def eid(eik: bytes, counter: int) -> bytes:
    window = (counter & 0xFFFFFFFF & ~(ROTATION - 1)).to_bytes(4, "big")
    block = b"\xff" * 11 + b"\x0a" + window + b"\x00" * 11 + b"\x0a" + window
    r = int.from_bytes(Cipher(algorithms.AES(eik), modes.ECB()).encryptor().update(block), "big")
    return int((SECP160r1.generator * (r % SECP160r1.order)).x()).to_bytes(20, "big")


def bws(*args: str) -> list[dict[str, str]]:
    result = subprocess.run(["bws", "secret", *args], capture_output=True, text=True, check=True)
    return json.loads(result.stdout) if result.stdout.strip() else []


def parse(buf: bytes) -> list[Field]:
    fields: list[Field] = []
    i = 0
    while i < len(buf):
        key, i = varint(buf, i)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, i = varint(buf, i)
            fields.append((number, value))
        elif wire == 2:
            length, i = varint(buf, i)
            fields.append((number, buf[i : i + length]))
            i += length
        elif wire == 1:
            i += 8
        elif wire == 5:
            i += 4
        else:
            raise SystemExit(f"unsupported protobuf wire type {wire}")
    return fields


def varint(buf: bytes, i: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        byte = buf[i]
        i += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, i
        shift += 7


def submessages(buf: bytes, number: int) -> list[list[Field]]:
    return [parse(value) for n, value in parse(buf) if n == number and isinstance(value, bytes)]


def first(fields: list[Field], number: int) -> int | bytes | None:
    return next((value for n, value in fields if n == number), None)


def instance(value: int | bytes | None) -> bytes:
    if not isinstance(value, bytes):
        raise SystemExit("device registration is missing a field the script expects")
    return value


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
