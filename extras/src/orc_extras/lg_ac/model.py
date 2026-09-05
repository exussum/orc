from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple


class ACState(NamedTuple):
    power: str | None = None
    mode: str | None = None
    fan_mode: str | None = None
    current_temperature: float | None = None
    temperature: float | None = None


class Certificate(NamedTuple):
    cert_pem: bytes
    key_pem: bytes


@dataclass(slots=True)
class TLVField:
    type_id: int
    value: int
    length: int = 0

    def encode(self) -> bytes:
        byte0 = (self.type_id >> 2) & 0xFF
        byte1 = (self.type_id & 3) << 6
        if self.length == 0:
            return bytes([byte0, byte1 | (self.value & 0x0F)])
        byte1 |= self.length << 4
        return bytes([byte0, byte1, *self.value.to_bytes(self.length, "big")])

    @classmethod
    def of(cls, type_id: int, value: int) -> TLVField:
        if value < 0x10:
            length = 0
        elif value < 0x100:
            length = 1
        elif value < 0x10000:
            length = 2
        else:
            length = 3
        return cls(type_id, value, length)


@dataclass(slots=True)
class DissectedPacket:
    fields: list[TLVField] = field(default_factory=list)
    remainder: bytes = b""

    def rebuild(self) -> bytes:
        return b"".join(f.encode() for f in self.fields) + self.remainder
