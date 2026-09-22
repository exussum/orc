import pytest

from orc import security

EIK = bytes.fromhex("0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
COUNTER = 0x12345678

# Golden vectors from BSkando/GoogleFindMy-HA (MIT), tests/test_eid_generator_variants.py.
VECTORS = [
    ("secp160r1", 0, "7bf149821dafae98259bfe53a87283c41d7b1b1c"),
    ("p256", 1, "72d4e4be2c6f3c1c5328f10d884ab58e0a474b06584d9c893b1e099853289cd9"),
    ("p256-truncated", 2, "72d4e4be2c6f3c1c5328f10d884ab58e0a474b06"),
]


@pytest.mark.parametrize("name,idx,expected", VECTORS, ids=[v[0] for v in VECTORS])
def test_fmdn_eids_golden_vector(name, idx, expected):
    assert security.fmdn_eids(EIK, COUNTER)[idx].hex() == expected


def test_fmdn_eids_constant_within_rotation_window():
    base = 2 * security.FMDN_ROTATION_SECONDS + 5
    assert security.fmdn_eids(EIK, base) == security.fmdn_eids(EIK, base + 1)
    assert security.fmdn_eids(EIK, base) != security.fmdn_eids(EIK, base + security.FMDN_ROTATION_SECONDS)


PARSE_CASES = [
    ("legacy frame", bytes([0x40]) + bytes(20) + bytes([0x7F]), bytes(20)),
    ("unwanted-tracking frame", bytes([0x41]) + bytes(20) + bytes([0x7F]), bytes(20)),
    ("modern frame", bytes([0x40]) + bytes(range(32)) + bytes([0x7F]), bytes(range(32))),
    ("legacy frame without flags", bytes([0x40]) + bytes(20), bytes(20)),
    ("eddystone uid frame", bytes([0x00]) + bytes(21), None),
    ("truncated frame", bytes([0x40]) + bytes(4), None),
    ("empty", b"", None),
]


@pytest.mark.parametrize("name,frame,expected", PARSE_CASES, ids=[c[0] for c in PARSE_CASES])
def test_fmdn_parse(name, frame, expected):
    assert security.fmdn_parse(frame) == expected


@pytest.mark.parametrize("name,idx", [(v[0], v[1]) for v in VECTORS], ids=[v[0] for v in VECTORS])
def test_fmdn_resolve_finds_the_broadcast_variant(name, idx):
    heard = {bytes(20), security.fmdn_eids(EIK, COUNTER)[idx]}
    window = range(
        COUNTER - 2 * security.FMDN_ROTATION_SECONDS, COUNTER + 2 * security.FMDN_ROTATION_SECONDS, security.FMDN_ROTATION_SECONDS
    )
    assert security.fmdn_resolve(heard, EIK, window) is True


def test_fmdn_resolve_misses_other_keys():
    heard = {security.fmdn_eids(bytes(32), COUNTER)[0]}
    assert security.fmdn_resolve(heard, EIK, [COUNTER]) is False
