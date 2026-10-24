import pytest

from orc import model as m


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (m.AcState(power=m.OFF), m.AcState(power=m.OFF, mode="cool", fan_mode="low", temperature=72), True),
        (m.AcState(power=m.ON, mode="cool"), m.AcState(power=m.ON, mode="cool", fan_mode="low", temperature=72), True),
        (m.AcState(power=m.ON, mode="cool"), m.AcState(power=m.ON, mode="fan_only", fan_mode="low", temperature=72), False),
        (m.AcState(power=m.ON, mode="cool", current_temperature=70), m.AcState(power=m.ON, mode="cool", current_temperature=75), True),
        (m.AcState(), m.AcState(), False),
        (m.AcState(mode="cool"), m.AcState(power=m.ON, mode="cool"), False),
    ],
)
def test_ac_state_eq(a, b, expected):
    assert (a == b) is expected
