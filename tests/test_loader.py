import re
from datetime import time, timedelta
from pathlib import Path

import pytest
from command_cfg import scalar

from orc import model as m
from orc.dal import interfaces
from orc.loader import ConfigError, load_plugin_config, parse_config, validate

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


def test_routine_skip_replay_flag():
    routines = parse("core").routines
    assert routines["ROUTINE_MEETING"].skip_replay is True
    assert routines["ROUTINE_RESET"].skip_replay is False


def test_themes_schedule_routines():
    themes = parse("core").themes
    assert [c.name for c in themes["work day"].configs] == ["Reset"]
    assert themes["work day"].configs[0].when == time(1, 0)
    assert themes["day off"].configs[0].when == m.SUNSET


def test_rooms_collect_member_states():
    parsed = parse("core")
    assert parsed.room_configs["Bedroom"].items == (m.Config(parsed.enums["Light"]["LAMP"], "on"),)


def test_volumes_are_bounded_ints():
    assert parse("core").audio_volumes == m.Volume(INFO=4, FATAL=10)


def test_settings_typed_and_defaulted():
    settings = parse("core").settings
    assert settings.lat == 40.7143
    assert settings.mqtt_host == "hub.test"
    assert settings.http_timeout == 5
    assert str(settings.tz) == "America/New_York"


def test_validate_missing_settings():
    with pytest.raises(
        ConfigError,
        match="Missing required settings: base_url, lan_domain, jobs_db, lat, long, audio_device, broadlink_codes, mqtt_host",
    ):
        validate(parse("validate_missing_settings"))


def test_validate_empty_setting():
    with pytest.raises(ConfigError, match="Missing required settings: mqtt_host"):
        validate(parse("validate_empty_setting"))


def test_validate_accepts_complete_config():
    parsed = parse("core")
    parsed.providers = parse("provider").providers
    validate(parsed)


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


def test_state_youtube_ids_stay_strings():
    parsed = parse("youtube_state")
    states = {name: cfg.items[0].state for name, cfg in parsed.ad_hoc_routines.items()}
    assert states == {"Music": "dQw4w9WgXcQ", "Numbers": "12345678901", "Volume": 40}


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

    plugin = next(p for p in parse("core").plugins if p.name == "Test Light")
    assert plugin.func is core_plugins.light_test
    assert plugin.section == "device"
    assert plugin.icon == "tv"
    assert plugin.delay == timedelta()


def test_load_plugin_config_missing_file():
    with pytest.raises(FileNotFoundError, match="no config 'plugins/foo.orc'"):
        load_plugin_config("foo", {}, "setting <key> <value>", {"setting": scalar(m.Settings.build)})


_PARSE_ERRORS = [
    ("device_type_not_defined", "name 'Foo' is not defined — device types must be defined and sealed first"),
    ("device_type_not_sealed", "name 'Foo' is not defined — device types must be defined and sealed first"),
    ("unknown_device_member", "type object 'Foo' has no attribute 'B'"),
    ("device_expression_syntax_error", "'(' was never closed"),
    ("add_before_define", "Unknown device type 'Foo'"),
    ("seal_before_define", "Unknown device type 'Foo'"),
    ("add_after_seal", "Device type 'Foo' is already sealed"),
    ("define_after_seal", "Device type 'Foo' is already sealed"),
    ("seal_after_seal", "Device type 'Foo' is already sealed"),
    ("only_after_seal", "Device type 'Foo' is already sealed"),
    ("unsealed_at_end", "Device types defined but never sealed: ['Foo']"),
    ("duplicate_member_names", "Duplicate names in 'Foo': {'A'}"),
    ("duplicate_device_ids", "Duplicate device id in 'Foo': {'h'}"),
    ("level_out_of_range", "Invalid parameter level='101'"),
    ("invalid_state", "Invalid state 'wibble'"),
    ("invalid_delay", "Invalid parameter delay='soon'"),
    ("invalid_snapshot", "Invalid parameter snapshot='lots'"),
    ("invalid_section", "Invalid parameter section='weird'"),
    ("time_not_hh_mm", "Invalid time 'noon'"),
    ("time_out_of_range", "Invalid time '25:00'"),
    ("plugin_import_failure", "Cannot load module 'not.a.module'"),
    ("theme_unknown_routine", "Unknown routine 'OTHER'"),
    ("append_unknown_routine", "Unknown routine 'R'"),
    ("unknown_trigger", "Unknown trigger 'NOPE'"),
    ("append_unknown_ad_hoc", "Unknown ad-hoc routine 'X'"),
    ("highlight_unknown_ad_hoc", "Unknown ad-hoc routine 'X'"),
    ("invalid_button_event", "Invalid button event 'clicked'"),
    ("non_numeric_button", "Invalid parameter button='one'"),
]


@pytest.mark.parametrize("case, error", _PARSE_ERRORS, ids=[case for case, _ in _PARSE_ERRORS])
def test_parse_error(case, error):
    with pytest.raises(ConfigError, match=re.escape(error)):
        parse(case)


def test_provider_imports_backends():
    from orc.dal.blaster import stub as blaster_stub
    from orc.dal.chromecast import stub as chromecast_stub
    from orc.dal.holiday import stub as holiday_stub
    from orc.dal.hubitat import stub as hubitat_stub
    from orc.dal.mqtt import stub as mqtt_stub
    from orc.dal.secrets import stub as secrets_stub
    from orc.dal.weather import stub as weather_stub

    providers = parse("provider").providers
    assert providers.secrets is secrets_stub
    assert providers.weather is weather_stub
    assert providers.holiday is holiday_stub
    assert providers.mqtt is mqtt_stub
    assert providers.chromecast is chromecast_stub
    assert providers.blaster is blaster_stub
    assert providers.hubitat is hubitat_stub


def test_provider_defaults_to_none():
    assert parse("core").providers == interfaces.Provider()


def test_validate_missing_providers():
    with pytest.raises(ConfigError, match="Missing required providers: secrets, weather, holiday, mqtt, chromecast, blaster, hubitat"):
        validate(parse("core"))


def test_validate_missing_routines():
    with pytest.raises(ConfigError, match="Missing required routines: ROUTINE_DEFAULT, ROUTINE_RESET"):
        validate(parse("validate_missing_routines"))


def test_validate_missing_themes():
    with pytest.raises(ConfigError, match="Missing required themes: day off, work day"):
        validate(parse("validate_missing_themes"))


def test_validate_requires_reset_routine_name():
    with pytest.raises(ConfigError, match="Missing required routine names: Reset"):
        validate(parse("validate_missing_reset_name"))
