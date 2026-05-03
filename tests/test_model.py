from enum import Enum

import pytest
from mistletoe import Document

from orc import config
from orc import model as m


class Light(Enum):
    a = 1
    b = 2
    c = 3


class Sound(Enum):
    x = 1


config.Light = Light


def _routines_md(rows):
    header = "| ID | Name | Expression | State | Mandatory |\n|----|------|------------|-------|-----------|\n"
    return "##### Routines\n\n" + header + "".join(rows) + "\n---\n"


def _themes_md(rows):
    header = "| Name | ID | Time |\n|------|----|------|\n"
    return "##### Themes\n\n" + header + "".join(rows) + "\n---\n"


def _rooms_md(rows):
    header = "| Room | IDs | State |\n|------|-----|-------|\n"
    return "##### Room Configs\n\n" + header + "".join(rows) + "\n---\n"


def test_squish_dim_then_off():
    cfg = (
        m.Config(Light.a, 10),
        m.Config(Light.a, config.ON),
        m.Config(Light.a, 20),
        m.Config(Light.a, config.ON),
        m.Config(Light.a, config.OFF),
    )
    assert m.squish(cfg) == (
        m.Config(Light.a, 20),
        m.Config(Light.a, config.OFF),
    )


def test_squish_just_off():
    cfg = (m.Config(Light.a, config.ON), m.Config(Light.a, config.OFF))
    assert m.squish(cfg) == (m.Config(Light.a, config.OFF),)


def test_squish_dim_on():
    cfg = (m.Config(Light.a, 20), m.Config(Light.a, config.ON))
    assert m.squish(cfg) == (
        m.Config(Light.a, 20),
        m.Config(Light.a, config.ON),
    )


def test_squish_0_on():
    cfg = (m.Config(Light.a, 0), m.Config(Light.a, config.ON))
    assert m.squish(cfg) == (
        m.Config(Light.a, 0),
        m.Config(Light.a, config.ON),
    )


def test_squish_just_on():
    cfg = (m.Config(Light.a, config.OFF), m.Config(Light.a, config.ON))
    assert m.squish(cfg) == (m.Config(Light.a, config.ON),)


def test_theme_squish_everything_off_start():
    routine = m.Configs(m.Config(Light, config.OFF), m.Config(Light.a, config.ON))
    assert m.squish_configs(routine) == m.Configs(
        m.Config(Light.a, config.ON, mandatory=False),
        m.Config(Light.b, config.OFF, mandatory=False),
        m.Config(Light.c, config.OFF, mandatory=False),
    )


def test_theme_squish_double_on():
    routine = m.Configs(m.Config(Light, config.ON), m.Config(Light.a, config.ON))
    assert m.squish_configs(routine) == m.Configs(
        m.Config(Light.a, config.ON, mandatory=False),
        m.Config(Light.b, config.ON, mandatory=False),
        m.Config(Light.c, config.ON, mandatory=False),
    )


def test_theme_squish_dim_then_off():
    routine = m.Configs(
        m.Config(Light, config.OFF),
        m.Config(Light.a, 10),
        m.Config(Light, config.OFF),
    )
    assert m.squish_configs(routine) == m.Configs(
        m.Config(Light.a, 10, mandatory=False),
        m.Config(Light.a, config.OFF, mandatory=False),
        m.Config(Light.b, config.OFF, mandatory=False),
        m.Config(Light.c, config.OFF, mandatory=False),
    )


def test_squish_configs_stop_then_volume():
    routine = m.Configs(
        m.Config(Sound, "stop"),
        m.Config(Sound, "stop"),
        m.Config(Sound.x, 10),
    )
    assert m.squish_configs(routine) == m.Configs(
        m.Config(Sound.x, "stop", mandatory=False),
        m.Config(Sound.x, 10, mandatory=False),
    )


def test_op_cmp_dim():
    assert m._op_cmp(m.Config(Light.a, 50)) == ("Light", -1)


def test_op_cmp_on():
    assert m._op_cmp(m.Config(Light.a, config.ON)) == ("Light", 0)


def test_op_cmp_off():
    assert m._op_cmp(m.Config(Light.a, config.OFF)) == ("Light", 1)


def test_op_cmp_sorts_dim_before_on_before_off():
    configs = [m.Config(Light.a, config.OFF), m.Config(Light.b, config.ON), m.Config(Light.c, 50)]
    assert sorted(configs, key=m._op_cmp) == [m.Config(Light.c, 50), m.Config(Light.b, config.ON), m.Config(Light.a, config.OFF)]


def test_op_cmp_sorts_by_class_name():
    configs = [m.Config(Sound.x, config.ON), m.Config(Light.a, config.ON)]
    assert sorted(configs, key=m._op_cmp) == [m.Config(Light.a, config.ON), m.Config(Sound.x, config.ON)]


def test_build_themes_succeeds_with_required():
    doc = Document(
        _routines_md(["| ROUTINE_RESET | Reset | Light | off | True |\n"])
        + _themes_md(["| work day | ROUTINE_RESET | 1:00 |\n", "| day off | ROUTINE_RESET | 23:00 |\n"])
    )
    themes = m.build_themes(doc, "Routines", "Themes", Light, Sound)
    assert set(themes) == {"work day", "day off"}


def test_build_themes_missing_reset_routine():
    doc = Document(
        _routines_md(["| ROUTINE_OFF | Lights Off | Light | off | True |\n"])
        + _themes_md(["| work day | ROUTINE_OFF | 1:00 |\n", "| day off | ROUTINE_OFF | 23:00 |\n"])
    )
    with pytest.raises(ValueError, match="Missing required routines.*Reset"):
        m.build_themes(doc, "Routines", "Themes", Light, Sound)


def test_build_themes_missing_required_theme():
    doc = Document(
        _routines_md(["| ROUTINE_RESET | Reset | Light | off | True |\n"]) + _themes_md(["| work day | ROUTINE_RESET | 1:00 |\n"])
    )
    with pytest.raises(ValueError, match="Missing required themes.*day off"):
        m.build_themes(doc, "Routines", "Themes", Light, Sound)


def test_build_config_succeeds_with_required():
    doc = Document(_rooms_md(["| Living Room | Light.a | on |\n", "| Bedroom | Light.b | on |\n"]))
    rooms = m.build_config(doc, "Room Configs", Light, Sound, required=("Living Room",))
    assert set(rooms) == {"Living Room", "Bedroom"}


def test_build_config_missing_required_room():
    doc = Document(_rooms_md(["| Bedroom | Light.b | on |\n"]))
    with pytest.raises(ValueError, match="Missing required entries.*Living Room"):
        m.build_config(doc, "Room Configs", Light, Sound, required=("Living Room",))
