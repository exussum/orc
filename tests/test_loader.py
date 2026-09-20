import re
from datetime import time, timedelta
from pathlib import Path

import pytest

from orc import model as m
from orc.dal import interfaces
from orc.kernel import engine, loader
from orc.kernel.loader import ConfigError, parse_config, validate

FIXTURE = Path(__file__).parent / "fixture"


def test_condition_system_is_unconditional():
    assert loader._conditions(None) == ()
    assert loader._conditions("SYSTEM") == ()


def test_condition_anyone():
    assert loader._conditions("ANYONE") == (engine.Is(m.AnyoneChannel(), True),)


def test_condition_weather_is_membership():
    assert loader._conditions("SUNNY") == (engine.In(m.WeatherChannel(), m.WeatherCondition.SUNNY),)


def test_condition_person():
    assert loader._conditions("alice") == (engine.Is(m.PersonChannel("alice"), True),)


def test_condition_holds_against_world():
    world = {m.AnyoneChannel(): True, m.PersonChannel("bob"): False}
    read = world.__getitem__
    assert loader._conditions("ANYONE")[0].holds(read)
    assert not loader._conditions("bob")[0].holds(read)


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
    assert parsed.routine["ROUTINE_RESET"].name == "Reset"
    assert parsed.routine["ROUTINE_RESET"].commands == (
        engine.Command(m.Devices(light), "off", tag="SYSTEM"),
        engine.Command(m.Devices(cc), "stop"),
    )


def test_routine_skip_replay_flag():
    routines = parse("core").routine
    assert m.SKIP_REPLAY_TAG in routines["ROUTINE_MEETING"].tags
    assert m.SKIP_REPLAY_TAG not in routines["ROUTINE_RESET"].tags


def test_themes_schedule_routines():
    themes = parse("core").theme
    assert [c.name for c in themes["work day"].configs] == ["Reset"]
    assert themes["work day"].configs[0].trigger == engine.At(time(1, 0))
    assert themes["day off"].configs[0].trigger == engine.At(m.SUNSET)


def test_rooms_collect_member_states():
    parsed = parse("core")
    assert parsed.room["Bedroom"].commands == (engine.Command(m.Devices(parsed.enums["Light"]["LAMP"]), "on"),)


def test_settings_typed_and_defaulted():
    settings = parse("core").setting
    assert settings.lat == 40.7143
    assert settings.mqtt_host == "hub.test"
    assert settings.http_timeout == 5
    assert str(settings.tz) == "America/New_York"


def test_validate_missing_settings():
    with pytest.raises(
        ConfigError,
        match="Missing required settings: base_url, lan_domain, jobs_db, lat, long, broadlink_codes, mqtt_host, "
        "warning_device, attention_device, emergency_device, emergency_routine",
    ):
        validate(parse("validate_missing_settings"))


def test_validate_empty_setting():
    with pytest.raises(ConfigError, match="Missing required settings: mqtt_host"):
        validate(parse("validate_empty_setting"))


def test_validate_unknown_emergency_routine():
    with pytest.raises(ConfigError, match=r"Unknown routine 'ROUTINE_EMERGENCY': expected one of \('ROUTINE_RESET', 'ROUTINE_DEFAULT'\)"):
        validate(parse("validate_unknown_emergency_routine"))


def test_validate_accepts_complete_config():
    parsed = parse("core")
    parsed.provider = parse("provider").provider
    validate(parsed)


def test_ad_hoc_define_with_inline_first_item():
    parsed = parse("core")
    silence = parsed.ad_hoc["Silence"]
    assert silence.commands == (engine.Command(m.Devices(parsed.enums["Chromecast"]["CC"]), "stop"),)
    assert silence.reset is False
    assert silence.section == "scene"


def test_ad_hoc_delay():
    dog = parse("core").ad_hoc["Dog"]
    assert dog.delay == timedelta(minutes=7)
    assert dog.reset is True


def test_ac_state_covers_every_ac_mode():
    assert {mode.name for mode in m.AcMode} <= set(m.AcState.__members__)
    assert all(m.AcState[mode.name] in m.AcState.ON for mode in m.AcMode)


def test_routines_accept_ac_commands():
    parsed = parse("ac_routine")
    assert [c.value for c in parsed.routine["R_AC"].commands] == [m.AcCommand(m.AcMode.COOL, "low", 75), m.OFF]


def test_state_youtube_ids_stay_strings():
    parsed = parse("youtube_state")
    states = {name: cfg.commands[0].value for name, cfg in parsed.ad_hoc.items()}
    assert states == {"Music": "dQw4w9WgXcQ", "Numbers": "12345678901", "Volume": 40}


def test_ad_hoc_append_extends_items():
    parsed = parse("core")
    assert parsed.ad_hoc["All Lights Off"].commands == (
        engine.Command(m.Devices(parsed.enums["Light"]), "off"),
        engine.Command(m.Devices(parsed.enums["Chromecast"]["CC"]), "stop"),
    )


def test_remote_repeats_device_with_ditto():
    parsed = parse("core")
    remote = parsed.enums["Button"]["REMOTE"]
    assert parsed.remote == (
        m.Remote(remote, 1, "pushed", "All Lights Off"),
        m.Remote(remote, 1, "held", "Silence"),
    )


def test_highlight_windows_reference_ad_hoc():
    assert parse("core").highlight == (("Silence", time(21, 0), time(23, 59)),)


def test_person_becomes_known_trigger():
    parsed = parse("core")
    assert parsed.person == {"Spence": [m.Person("host9", "aa:bb")]}
    assert parsed.routine["ROUTINE_DEFAULT"].commands[-1].tag == "Spence"


def test_plugin_command_imports_callable():
    from orc import plugins as core_plugins

    plugin = next(p for p in parse("core").plugins if p.name == "Test Light")
    assert plugin.func is core_plugins.light_test
    assert plugin.section == "device"
    assert plugin.icon == "tv"
    assert plugin.delay == timedelta()


_PARSE_ERRORS = [
    ("device_type_not_defined", "name 'Foo' is not defined — device types must be defined and sealed first"),
    ("unknown_device_member", "Unknown Foo device 'B': expected one of ['A']"),
    ("device_expression_syntax_error", "'(' was never closed"),
    ("add_before_define", "Unknown device type 'Foo'"),
    ("add_after_seal", "Device type 'Foo' is already sealed"),
    ("unsealed_at_end", "Device types defined but never sealed: ['Foo']"),
    ("duplicate_member_names", "Duplicate names in 'Foo': {'A'}"),
    ("duplicate_device_ids", "Duplicate device id in 'Foo': {'h'}"),
    ("invalid_state", "Invalid state 'wibble'"),
    ("invalid_ac_command", "Invalid AC command 'chill:low:75'"),
    ("ac_command_non_ac", "AC command cool:low:75 applies only to AC devices, got 'Foo.A'"),
    ("ac_device_bad_state", "AC devices take a mode:fan:temp command, 'on', or 'off', got 'stop'"),
    ("invalid_delay", "invalid literal for int() with base 10: 'soon'"),
    ("invalid_snapshot", "invalid literal for int() with base 10: 'lots'"),
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
    ("non_numeric_button", "invalid literal for int() with base 10: 'one'"),
]


@pytest.mark.parametrize("case, error", _PARSE_ERRORS, ids=[case for case, _ in _PARSE_ERRORS])
def test_parse_error(case, error):
    with pytest.raises(ConfigError, match=re.escape(error)):
        parse(case)


def test_provider_imports_backends():
    from orc.dal.audio import stub as audio_stub
    from orc.dal.blaster import stub as blaster_stub
    from orc.dal.chromecast import stub as chromecast_stub
    from orc.dal.holiday import stub as holiday_stub
    from orc.dal.hubitat import stub as hubitat_stub
    from orc.dal.mqtt import stub as mqtt_stub
    from orc.dal.secrets import stub as secrets_stub
    from orc.dal.weather import stub as weather_stub

    providers = parse("provider").provider
    assert providers.secrets is secrets_stub
    assert providers.weather is weather_stub
    assert providers.holiday is holiday_stub
    assert providers.mqtt is mqtt_stub
    assert providers.chromecast is chromecast_stub
    assert providers.blaster is blaster_stub
    assert providers.hubitat is hubitat_stub
    assert providers.audio is audio_stub


def test_provider_defaults_to_none():
    assert parse("core").provider == interfaces.Provider()


def test_validate_missing_providers():
    with pytest.raises(ConfigError, match="Missing required providers: weather, holiday, blaster"):
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
