from datetime import time
from pathlib import Path

from orc import model as m
from orc.loader import parse_config, validate

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
