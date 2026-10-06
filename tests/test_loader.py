import re
from datetime import UTC, datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from orc_engine import cast, engine
from orc_engine import model as em

from orc import model as m
from orc.dal.audio import pyaudio
from orc.dal.chromecast import pychromecast
from orc.dal.hubitat import http as hubitat_http
from orc.dal.mqtt import paho as mqtt_paho
from orc.dal.push import webpush
from orc.dal.secrets import bws
from orc.dal.secrets import stub as secrets_stub
from orc.kernel import loader
from orc.kernel.loader import ConfigError, parse_config, validate

FIXTURE = Path(__file__).parent / "fixture"


def test_condition_system_is_unconditional():
    assert loader._condition(None) == em.And()
    assert loader._condition("SYSTEM") == em.And()


def test_condition_anyone():
    assert loader._condition("ANYONE") == m.Anyone()


def test_condition_weather_is_membership():
    assert loader._condition("SUNNY") == m.Weather(m.WeatherCondition.SUNNY)


def test_condition_person():
    assert loader._condition("alice") == m.Present("alice")


def test_condition_holds_against_world():
    world = {m.AnyoneSubject(): True, m.PersonSubject("bob"): False}
    seen = engine.Runtime(UTC).world(world.__getitem__)
    assert loader._condition("ANYONE").holds(seen)
    assert not loader._condition("bob").holds(seen)


def parse(case, **kwargs):
    return parse_config((FIXTURE / f"{case}.orc").read_text(), **kwargs)


def test_devices_build_enums_with_rooms():
    light = parse("core").enums["Light"]
    assert [e.name for e in light] == ["LAMP", "DESK"]
    assert light["LAMP"].room == "Bedroom"
    assert light["DESK"].room == m.UNASSIGNED_ROOM


def test_devices_without_zigbee_config_get_generated_ids_and_are_virtual():
    parsed = parse("core")
    light = parsed.enums["Light"]
    assert len({light["LAMP"].value, light["DESK"].value}) == 2
    assert all(len(light[name].value) == 32 for name in ("LAMP", "DESK"))
    assert {light["LAMP"], light["DESK"]} <= parsed.virtual_devices


def test_devices_resolve_zigbee_ids():
    parsed = parse("core", zigbee_config={"h1": ("5", frozenset())})
    assert parsed.enums["Light"]["LAMP"].value == "5"
    assert parsed.enums["Light"]["LAMP"] not in parsed.virtual_devices


def test_device_only_defines_and_seals_in_one_line():
    chromecast = parse("core").enums["Chromecast"]
    assert chromecast["CC"].value == "host3"
    assert chromecast["CC"].room == "Living"


def test_routines_append_devices_and_triggers():
    parsed = parse("core")
    light, cc = parsed.enums["Light"], parsed.enums["Chromecast"]["CC"]
    assert parsed.routine["ROUTINE_RESET"].name == "Reset"
    assert parsed.routine["ROUTINE_RESET"].commands == (
        em.Command(m.Devices(light), "off", tag="SYSTEM"),
        em.Command(m.Devices(cc), "stop"),
    )


def test_routine_skip_replay_flag():
    routines = parse("core").routine
    assert m.SKIP_REPLAY_TAG in routines["ROUTINE_MEETING"].tags
    assert m.SKIP_REPLAY_TAG not in routines["ROUTINE_RESET"].tags


def test_themes_schedule_routines():
    themes = parse("core").theme
    assert [(e.when, e.routine.name) for e in themes["work day"].entries] == [(time(1, 0), "Reset")]
    assert [(e.when, e.routine.name) for e in themes["day off"].entries] == [(m.SUNSET, "Welcome")]


def test_rooms_collect_member_states():
    parsed = parse("core")
    assert parsed.room["Bedroom"].commands == (em.Command(m.Devices(parsed.enums["Light"]["LAMP"]), "on"),)


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


def test_tag_lines_parse():
    assert parse("core").tag == [m.BleTag("Alice", "EIK_ALICE", "2026-01-02T03:04:05+00:00")]


def test_validate_unknown_tag_person():
    with pytest.raises(ConfigError, match="Unknown person 'Bob' in tag line"):
        validate(parse("tag_unknown_person"))


def test_validate_bad_tag_pair_date():
    with pytest.raises(ConfigError, match="Invalid pair_date 'yesterday' in tag line"):
        validate(parse("tag_bad_pair_date"))


def test_ble_keys_empty_without_secrets():
    tags = [m.BleTag("Alice", "EIK_ALICE", "2026-01-02T03:04:05+00:00")]
    assert loader.ble_keys(tags, m.Secrets(), timezone.utc) == {}


def test_ble_keys_derives_eik_and_anchor():
    secrets = m.Secrets(other={"EIK_ALICE": "ab" * 32})
    tags = [m.BleTag("Alice", "EIK_ALICE", "2026-01-02T03:04:05+00:00")]
    anchor = int(datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc).timestamp())
    assert loader.ble_keys(tags, secrets, timezone.utc) == {"Alice": m.BleKey(bytes.fromhex("ab" * 32), anchor)}


def test_ble_keys_naive_pair_date_reads_config_tz():
    secrets = m.Secrets(other={"EIK_ALICE": "ab" * 32})
    tags = [m.BleTag("Alice", "EIK_ALICE", "2026-01-02T03:04:05")]
    anchor = int(datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc).timestamp())
    assert loader.ble_keys(tags, secrets, timezone.utc)["Alice"].anchor == anchor


@pytest.mark.parametrize(
    ("other", "expected"),
    [
        ({"EIK_ALICE": "ab" * 32}, {}),
        ({"EIK_ALICE": "abcd"}, {"EIK_ALICE": "expected hex32"}),
        ({"EIK_ALICE": ""}, {"EIK_ALICE": "not set"}),
        ({}, {"EIK_ALICE": "not set"}),
    ],
)
def test_check_secrets(other, expected):
    assert loader.check_secrets(m.Secrets(other=other), {"EIK_ALICE": cast.hex32}) == expected


def test_check_secrets_reads_typed_fields():
    assert loader.check_secrets(m.Secrets(vapid_private_key="short"), {"vapid_private_key": cast.key32}) == {
        "vapid_private_key": "expected key32"
    }


def test_secret_needs_gathers_providers_plugins_and_tags():
    registry = SimpleNamespace(secrets={"PLUGIN_KEY": cast.nonblank})
    providers = (SimpleNamespace(REQUIRED_SECRETS={"hubitat_access_token": cast.uuid}), SimpleNamespace(REQUIRED_SECRETS={}), None)
    tags = [m.BleTag("Alice", "EIK_ALICE", "2026-01-02T03:04:05+00:00")]
    assert loader.secret_needs(registry, providers, tags) == {
        "hubitat_access_token": cast.uuid,
        "PLUGIN_KEY": cast.nonblank,
        "EIK_ALICE": cast.hex32,
    }


@pytest.mark.parametrize(
    ("shape", "value", "ok"),
    [
        (cast.uuid, "6b6a9c2e-1c1c-4c2a-9c4a-1e1f1a1b1c1d", True),
        (cast.uuid, "not-a-uuid", False),
        (cast.url, "https://example.com/holidays.json", True),
        (cast.url, "example.com/holidays.json", False),
        (cast.key32, "A" * 43, True),
        (cast.key32, "AAAA", False),
        (cast.key32, "not base64!", False),
        (cast.hex32, "ab" * 32, True),
        (cast.hex32, "abcd", False),
        (cast.pem_cert, secrets_stub.fetch_secrets().other["X_CERT"], True),
        (cast.pem_cert, "junk", False),
        (cast.pem_key, secrets_stub.fetch_secrets().other["X_KEY"], True),
        (cast.pem_key, "junk", False),
        (cast.nonblank, "anything", True),
        (cast.nonblank, "", False),
    ],
)
def test_secret_casts(shape, value, ok):
    if ok:
        shape(value)
    else:
        with pytest.raises(ValueError):
            shape(value)


def test_ad_hoc_define_with_inline_first_item():
    parsed = parse("core")
    silence = parsed.ad_hoc["Silence"]
    assert silence.commands == (em.Command(m.Devices(parsed.enums["Chromecast"]["CC"]), "stop"),)
    assert silence.reset is False
    assert silence.section is None


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
        em.Command(m.Devices(parsed.enums["Light"]), "off"),
        em.Command(m.Devices(parsed.enums["Chromecast"]["CC"]), "stop"),
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
    assert parsed.person == {"Alice": [m.Person("host9", "aa:bb")]}
    assert parsed.routine["ROUTINE_DEFAULT"].commands[-1].tag == "Alice"


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


def test_unnamed_providers_default_to_the_real_backend_or_nothing():
    provider = parse("core").provider
    assert (provider.secrets, provider.mqtt, provider.chromecast, provider.hubitat, provider.audio, provider.push) == (
        bws,
        mqtt_paho,
        pychromecast,
        hubitat_http,
        pyaudio,
        webpush,
    )
    assert (provider.weather, provider.holiday, provider.blaster) == (None, None, None)


def test_validate_missing_providers():
    with pytest.raises(ConfigError, match="Missing required providers: weather, holiday, blaster"):
        validate(parse("core"))


def test_validate_missing_routines():
    with pytest.raises(ConfigError, match="Missing required routines: ROUTINE_DEFAULT, ROUTINE_RESET"):
        validate(parse("validate_missing_routines"))


def test_validate_polar_sun_times():
    with pytest.raises(ConfigError, match="Latitude 70.0 has days without a sunrise or sunset: Welcome"):
        validate(parse("validate_polar_sun_times"))


def test_validate_missing_themes():
    with pytest.raises(ConfigError, match="Missing required themes: day off, work day"):
        validate(parse("validate_missing_themes"))


def test_validate_requires_reset_routine_name():
    with pytest.raises(ConfigError, match="Missing required routine names: Reset"):
        validate(parse("validate_missing_reset_name"))
