from datetime import datetime, timedelta

from orc_extras.memleak import Sample, rank

T0 = datetime(2026, 10, 13, 0, 5)
KIB = 1024


def test_rank_puts_steady_growth_first():
    sizes = [
        {"leak": 10 * KIB, "once": 10 * KIB, "tiny": 10 * KIB},
        {"leak": 20 * KIB, "once": 200 * KIB, "tiny": 11 * KIB},
        {"leak": 30 * KIB, "once": 200 * KIB, "tiny": 12 * KIB},
        {"leak": 40 * KIB, "once": 200 * KIB, "tiny": 13 * KIB},
    ]
    rows = rank([Sample(T0 + timedelta(hours=h), s) for h, s in enumerate(sizes)], 8.0)
    assert [(r.location, r.grown, r.intervals) for r in rows] == [("leak", 3, 3), ("once", 1, 3)]
    assert rows[0].kib_per_hour == 10
    assert rows[1].since_boot_kib == 190
