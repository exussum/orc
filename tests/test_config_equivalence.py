"""Both sample configs must describe the same system until config.md is removed."""

from pathlib import Path

import orc
from orc.loader import parse_config, validate

_ORC = (Path(__file__).parent.parent / "src" / "config.orc").read_text()


def ref(what):
    return what.__name__ if isinstance(what, type) else (type(what).__name__, what.name)


def items(configs):
    return [(ref(c.what), c.state, c.trigger) for c in configs.items]


def test_orc_config_validates():
    validate(parse_config(_ORC))


def test_routines_agree():
    parsed = parse_config(_ORC)
    assert parsed.routines.keys() == orc.config.routines.keys()
    for key, routine in orc.config.routines.items():
        assert (parsed.routines[key].name, items(parsed.routines[key])) == (routine.name, items(routine))


def test_themes_agree():
    parsed = parse_config(_ORC)
    assert parsed.themes.keys() == orc.config.themes.keys()
    for key, theme in orc.config.themes.items():
        assert [(r.name, r.when) for r in parsed.themes[key].configs] == [(r.name, r.when) for r in theme.configs]


def test_room_configs_agree():
    parsed = parse_config(_ORC)
    assert {name: items(c) for name, c in parsed.room_configs.items()} == {name: items(c) for name, c in orc.config.room_configs.items()}


def test_ad_hoc_routines_agree():
    parsed = parse_config(_ORC)
    assert parsed.ad_hoc_routines.keys() == orc.config.ad_hoc_routines.keys()
    for key, config in orc.config.ad_hoc_routines.items():
        ours = parsed.ad_hoc_routines[key]
        assert (items(ours), ours.snapshot, ours.delay, ours.section, ours.reset) == (
            items(config),
            config.snapshot,
            config.delay,
            config.section,
            config.reset,
        )


def test_buttons_agree():
    parsed = parse_config(_ORC)
    assert {(ref(d), b, e): a for (d, b, e), a in parsed.buttons.items()} == {
        (ref(d), b, e): a for (d, b, e), a in orc.config.buttons.items()
    }


def test_highlights_volumes_people_agree():
    parsed = parse_config(_ORC)
    assert tuple(parsed.button_highlight_configs) == tuple(orc.config.button_highlight_configs)
    assert parsed.audio_volumes._asdict() == orc.config.audio_volumes
    assert dict(parsed.people) == dict(orc.config.people)


def test_plugins_agree():
    parsed = parse_config(_ORC)
    assert parsed.plugins.keys() == orc.config.plugins.keys()
    for key, plugin in orc.config.plugins.items():
        ours = parsed.plugins[key]
        assert (ours.func, ours.section, ours.icon, ours.delay) == (plugin.func, plugin.section, plugin.icon, plugin.delay)
