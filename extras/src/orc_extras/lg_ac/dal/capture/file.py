from __future__ import annotations

import json
import time
from pathlib import Path

_path: Path | None = None


def configure(path: str) -> None:
    global _path
    _path = Path(path)
    _path.write_text("")  # start a fresh capture


def record(topic: str, payload: bytes) -> None:
    if _path is None:
        return
    entry = {"ts": time.time(), "kind": "wire", "topic": topic, "payload": payload.hex()}
    with _path.open("a") as handle:
        handle.write(json.dumps(entry) + "\n")
