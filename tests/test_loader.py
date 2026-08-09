from datetime import time, timedelta
from pathlib import Path

import pytest

from orc import model as m
from orc.loader import load_plugin_config, parse_config, validate

FIXTURE = Path(__file__).parent / "fixture"


def parse(case, **kwargs):
    return parse_config((FIXTURE / f"{case}.orc").read_text(), **kwargs)


def test_devices_build_enums_with_rooms():
    light = parse("core").enums["Light"]
    assert [e.name for e in light] == ["LAMP", "DESK"]
    assert light["LAMP"].room == "Bedroom"
    assert light["DESK"].room is None


def test_devices_without_zigbee_config_get_virtual_ids():
    light = parse("core").enums["Light"]
    assert (light["LAMP"].value, light["DESK"].value) == (-1, -2)


def test_devices_resolve_zigbee_ids():
    light = parse("core", zigbee_config={"h1": (5, frozenset())}).enums["Light"]
    assert light["LAMP"].value == 5


def test_device_only_defines_and_seals_in_one_line():
    chromecast = parse("core").enums["Chromecast"]
    assert chromecast["CC"].value == "host3"
    assert chromecast["CC"].room == "Living"


def test_routines_append_devices_and_triggers():
    parsed = parse("core")
    light, cc = parsed.enums["Light"], parsed.enums["Chromecast"]["CC"]
    assert parsed.routines["ROUTINE_RESET"].name == "Reset"
    assert parsed.routines["ROUTINE_RESET"].items == (
        m.Config(light, "off", trigger="SYSTEM"),
        m.Config(cc, "stop"),
    )


def test_themes_schedule_routines():
    themes = parse("core").themes
    assert [c.name for c in themes["work day"].configs] == ["Reset"]
    assert themes["work day"].configs[0].when == time(1, 0)
    assert themes["day off"].configs[0].when == m.SUNSET


def test_rooms_collect_member_states():
    parsed = parse("core")
    assert parsed.room_configs["Bedroom"].items == (m.Config(parsed.enums["Light"]["LAMP"], "on"),)


def test_volumes_are_bounded_ints():
    assert parse("core").audio_volumes == {"INFO": 4, "FATAL": 10}


def test_validate_accepts_complete_config():
    validate(parse("core"))


def test_ad_hoc_define_with_inline_first_item():
    parsed = parse("core")
    silence = parsed.ad_hoc_routines["Silence"]
    assert silence.items == (m.Config(parsed.enums["Chromecast"]["CC"], "stop"),)
    assert silence.reset is False
    assert silence.section == "scene"


def test_ad_hoc_delay():
    dog = parse("core").ad_hoc_routines["Dog"]
    assert dog.delay == timedelta(minutes=7)
    assert dog.reset is True


def test_ad_hoc_append_extends_items():
    parsed = parse("core")
    assert parsed.ad_hoc_routines["All Lights Off"].items == (
        m.Config(parsed.enums["Light"], "off"),
        m.Config(parsed.enums["Chromecast"]["CC"], "stop"),
    )


def test_button_map_repeats_device_with_ditto():
    parsed = parse("core")
    remote = parsed.enums["Button"]["REMOTE"]
    assert parsed.buttons == {
        (remote, 1, "pushed"): "All Lights Off",
        (remote, 1, "held"): "Silence",
    }


def test_highlight_windows_reference_ad_hoc():
    assert parse("core").button_highlight_configs == (("Silence", time(21, 0), time(23, 59)),)


def test_person_becomes_known_trigger():
    parsed = parse("core")
    assert parsed.people == {"Spence": [m.Person("host9", "aa:bb")]}
    assert parsed.routines["ROUTINE_DEFAULT"].items[-1].trigger == "Spence"


def test_plugin_command_imports_callable():
    from orc import plugins as core_plugins

    plugin = parse("core").plugins["Test Light"]
    assert plugin.func is core_plugins.light_test
    assert plugin.section == "device"
    assert plugin.icon == "tv"
    assert plugin.delay == timedelta()


def test_load_plugin_config_missing_file():
    with pytest.raises(FileNotFoundError, match="no config 'plugins/foo.orc'"):
        load_plugin_config("foo", {}, "setting <key> <value>")
