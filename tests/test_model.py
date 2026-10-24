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


def test_secrets_update_in_place_names_changes():
    secrets = m.Secrets(mqtt_user="a", other={"X": "1", "Y": "2"})
    other = secrets.other
    assert secrets.update(m.Secrets(mqtt_user="b", other={"X": "1", "Z": "3"})) == {"mqtt_user", "Y", "Z"}
    assert secrets.mqtt_user == "b"
    assert secrets.other is other
    assert secrets.other == {"X": "1", "Z": "3"}
