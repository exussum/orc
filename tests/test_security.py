# Copyright (C) 2025-2026 exussum
# Golden vectors from BSkando's GoogleFindMy-HA, GPL-3.0,
# tests/test_eid_generator_variants.py: https://github.com/BSkando/GoogleFindMy-HA.
#
# This file is part of orc.
#
# orc is free software: you can redistribute it and/or modify it under the terms
# of the GNU General Public License version 3 as published by the Free Software
# Foundation.
#
# orc is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
# without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
# PURPOSE. See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# orc. If not, see <https://www.gnu.org/licenses/>.

import pytest

from orc import security

EIK = bytes.fromhex("0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
COUNTER = 0x12345678

VECTORS = [
    ("secp160r1", 0, "7bf149821dafae98259bfe53a87283c41d7b1b1c"),
    ("p256", 1, "72d4e4be2c6f3c1c5328f10d884ab58e0a474b06584d9c893b1e099853289cd9"),
    ("p256-truncated", 2, "72d4e4be2c6f3c1c5328f10d884ab58e0a474b06"),
]


PARSE_CASES = [
    ("legacy frame", bytes([0x40]) + bytes(20) + bytes([0x7F]), bytes(20)),
    ("unwanted-tracking frame", bytes([0x41]) + bytes(20) + bytes([0x7F]), bytes(20)),
    ("modern frame", bytes([0x40]) + bytes(range(32)) + bytes([0x7F]), bytes(range(32))),
    ("legacy frame without flags", bytes([0x40]) + bytes(20), bytes(20)),
    ("eddystone uid frame", bytes([0x00]) + bytes(21), None),
    ("truncated frame", bytes([0x40]) + bytes(4), None),
]


@pytest.mark.parametrize("name,idx,expected", VECTORS, ids=[v[0] for v in VECTORS])
def test_fmdn_eids_golden_vector(name, idx, expected):
    assert security.fmdn_eids(EIK, COUNTER)[idx].hex() == expected


def test_fmdn_eids_constant_within_rotation_window():
    base = 2 * security.FMDN_ROTATION_SECONDS + 5
    assert security.fmdn_eids(EIK, base) == security.fmdn_eids(EIK, base + 1)
    assert security.fmdn_eids(EIK, base) != security.fmdn_eids(EIK, base + security.FMDN_ROTATION_SECONDS)


@pytest.mark.parametrize("name,frame,expected", PARSE_CASES, ids=[c[0] for c in PARSE_CASES])
def test_fmdn_parse(name, frame, expected):
    assert security.fmdn_parse(frame) == expected
