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
def test_ac_state_fuzzy_eq(a, b, expected):
    assert m.fuzzy_eq(a, b) is expected


def test_light_state_fuzzy_eq_within_a_mired():
    assert m.fuzzy_eq(m.LightState(color_temperature=3000), m.LightState(color_temperature=3003))
    assert not m.fuzzy_eq(m.LightState(color_temperature=3000), m.LightState(color_temperature=3100))
    assert m.fuzzy_eq(m.LightState(switch=m.OFF, level=10), m.LightState(switch=m.OFF, level=90))


def test_fuzzy_eq_is_false_across_kinds():
    assert not m.fuzzy_eq(m.AcState(power=m.ON), m.LightState(switch=m.ON))
    assert not m.fuzzy_eq(m.LightState(switch=m.ON), "on")


def test_fuzzy_eq_uses_the_state_rule_or_equality():
    assert m.fuzzy_eq(m.LightState(color_temperature=3000), m.LightState(color_temperature=3003))
    assert m.fuzzy_eq(m.AcState(power=m.ON), m.AcState(power=m.ON, mode="cool"))
    assert m.fuzzy_eq(None, None)
    assert not m.fuzzy_eq(None, m.AcState(power=m.ON))
    assert not m.fuzzy_eq(m.AcState(power=m.ON), None)
    assert m.fuzzy_eq("playing", "playing")
    assert not m.fuzzy_eq("playing", "paused")


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [(0, 0, True), (0, 3000, False), (3000, 0, False), (3000, 3003, True), (3000, 3100, False)],
)
def test_color_temperature_compare_tolerates_zero(a, b, expected):
    assert m._within_a_mired(a, b) is expected


def test_state_eq_is_exact_where_fuzzy_eq_is_not():
    assert m.AcState(power=m.ON, mode="cool") != m.AcState(power=m.ON, mode="cool", fan_mode="low", temperature=72)
    assert m.AcState(power=m.OFF) != m.AcState(power=m.OFF, mode="cool")
    assert m.AcState(power=m.ON, mode="cool", current_temperature=70) == m.AcState(power=m.ON, mode="cool", current_temperature=75)
    assert m.LightState(color_temperature=3000) != m.LightState(color_temperature=3003)
    assert m.LightState(switch=m.OFF, level=10) != m.LightState(switch=m.OFF, level=90)


def test_a_state_with_nothing_said_equals_and_hashes_like_itself():
    assert m.AcState() == m.AcState()
    assert m.LightState() == m.LightState()
    assert len({m.AcState(), m.AcState(), m.LightState(), m.LightState()}) == 2
    assert len({m.AcState(mode="cool"), m.AcState(mode="cool")}) == 1


def test_states_that_differ_only_by_an_unsaid_field_stay_distinct_in_a_set():
    assert len({m.AcState(power=m.ON), m.AcState(power=m.ON, mode="cool")}) == 2
    assert {m.AcState(power=m.ON), m.AcState(power=m.ON, mode="cool")} - {m.AcState(power=m.ON, mode="cool")} == {m.AcState(power=m.ON)}
    assert len({m.LightState(color_temperature=3000), m.LightState(color_temperature=3003)}) == 2


def test_state_hash_of_valid_states():
    assert hash(m.AcState(power=m.ON, mode="cool", current_temperature=70)) == hash(
        m.AcState(power=m.ON, mode="cool", current_temperature=75)
    )
    assert hash(m.AcState(power=m.ON, mode="cool")) == hash((m.ON, "cool", None, None))
    assert hash(m.LightState(switch=m.ON, level=50)) == hash(m.LightState(switch=m.ON, level=50))
    assert hash(m.LightState(level=50)) == hash((None, 50, None, None, None, None))
    assert len({m.LightState(switch=m.ON), m.LightState(switch=m.ON)}) == 1
