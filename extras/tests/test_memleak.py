from datetime import datetime, timedelta

from orc_extras.memleak import Sample, rank

T0 = datetime(2026, 10, 13, 0, 5)
KB = 1024


def test_rank_puts_steady_growth_first():
    sizes = [
        {"leak": 10 * KB, "once": 10 * KB, "tiny": 10 * KB},
        {"leak": 20 * KB, "once": 200 * KB, "tiny": 11 * KB},
        {"leak": 30 * KB, "once": 200 * KB, "tiny": 12 * KB},
        {"leak": 40 * KB, "once": 200 * KB, "tiny": 13 * KB},
    ]
    rows = rank([Sample(T0 + timedelta(hours=h), s) for h, s in enumerate(sizes)], 8.0)
    assert [(r.location, r.grown, r.intervals) for r in rows] == [("leak", 3, 3), ("once", 1, 3)]
    assert rows[0].kb_per_hour == 10
    assert rows[1].since_boot_kb == 190
