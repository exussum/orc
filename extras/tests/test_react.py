from datetime import UTC, timedelta
from types import SimpleNamespace

import pytest
from command_cfg import ConfigError
from flask import Flask
from orc_engine import engine
from orc_engine import model as em
from orc_extras import react
from orc_extras.react import model, plugins, web

import orc
from orc import api
from orc import model as m
from orc.dal.mqtt import hubitat
from orc.model import DeviceEnum
from tests.conftest import NOW, plugin_config

_PAUSE_ERRORS = [
    ("react_pause_zero", ConfigError, "Invalid --pause 0"),
    ("react_pause_mismatch", ValueError, "same --pause"),
]


class Light(DeviceEnum):
    lamp = "1"
    desk = "2"


class Ac(DeviceEnum):
    living = "clip-1"


class Chromecast(DeviceEnum):
    tv = "host9"


class Sensor(DeviceEnum):
    living = "5"


DEVICE_ENUMS = {"Light": Light, "AC": Ac, "Chromecast": Chromecast, "Sensor": Sensor}


@pytest.fixture
def ctx(ctx, freezegun_clock):
    ctx.engine = engine.Runtime(UTC)
    ctx.api.reader.return_value = _world_read(ctx)
    ctx.api.squish.side_effect = lambda commands, entry: m.squish(commands)
    ctx.api.capture_acs.return_value = (m.AcStatus(Ac.living, m.AcState(power=m.OFF)),)
    ctx.config.registry = orc.config.registry
    ctx.api.dewpoint.side_effect = api.dewpoint
    ctx.plugin_state = {react: model.State((), {}, {})}
    return ctx


@pytest.fixture
def react_rules(ctx):
    ctx.config.plugin_configs = plugin_config(react, "react")
    rules = react.setup(ctx)
    ctx.sources = ctx.api.add_listener.call_args.args[0].args[1]
    return rules


@pytest.fixture
def ruleset(ctx):
    def ruleset(watches, sources):
        ctx.plugin_state[react].watches = tuple(watches)
        ctx.sources = sources

    return ruleset


@pytest.fixture
def device_report(ctx):
    def device_report(device_id, attributes, old=None):
        name = next((e.name for cls in DEVICE_ENUMS.values() for e in cls if e.value == device_id), device_id)
        state = m.DeviceState(m.Device(device_id, name, "hubitat"), attributes, None)
        known = ctx.api.device_states.return_value
        others = [s for s in known if s.device.id != device_id] if isinstance(known, list) else []
        ctx.api.device_states.return_value = [*others, state]
        attribute = next(iter(attributes))
        plugins._on_event(ctx, ctx.sources, m.Status(state.device, attribute, old, attributes[attribute], hubitat.HubitatSource.HUBITAT))

    return device_report


@pytest.fixture
def pending_jobs(ctx, react_rules, device_report):
    device_report("1", {"switch": m.ON}, m.OFF)  # rule 0 has --delay, so it goes pending
    ctx.scheduler.reset_mock()


@pytest.fixture
def ac_report(ctx):
    def ac_report(state):
        ctx.api.capture_acs.return_value = (m.AcStatus(Ac.living, state),)

    return ac_report


@pytest.fixture
def dispatches(ctx):
    return lambda: [(c.subject.one(), c.value) for c in ctx.api.dispatch.call_args.args[0]]


@pytest.fixture
def deferred_run(ctx):
    def deferred_run():
        deferred, device = _payload(ctx.scheduler.once.call_args)
        plugins._run_react.__wrapped__(deferred, device, ctx=ctx)

    return deferred_run


# The fixture's first line (`react Light ...`) fans out to lamp + desk, so the
# compiled rules are: 0 lamp/on, 1 desk/on, 2..7 the single-device lines 2..7.
def test_config_compiles_rules(ctx, react_rules):
    rules = react_rules
    assert len(rules) == 8  # line 1 fans out to lamp + desk; lines 2..7 are single-device
    assert rules[0].condition == model.Transition(model.MqttDeviceSubject(Light.lamp, "switch"), m.ON)
    assert rules[1].condition == model.Transition(model.MqttDeviceSubject(Light.desk, "switch"), m.ON)
    assert rules[0].rule.steps[0].command == em.Command(m.Devices(Light.lamp), m.OFF)
    assert rules[0].delay == timedelta(minutes=10)
    assert ctx.api.add_listener.call_args.args[0].args[1] == {"1": Light.lamp, "2": Light.desk, "5": Sensor.living}


def test_switch_on_schedules_reaction(ctx, react_rules, device_report):
    device_report("1", {"switch": m.ON}, m.OFF)
    call = ctx.scheduler.once.call_args
    assert call.args[0] is plugins._run_react
    assert call.kwargs["id"].startswith("react-")
    assert _payload(call)[1] == m.Device("1", "lamp", "hubitat")


def test_switch_already_on_schedules_nothing(ctx, react_rules, device_report):
    device_report("1", {"switch": m.ON}, m.ON)
    ctx.scheduler.once.assert_not_called()


def test_switch_off_cancels_pending_jobs(ctx, pending_jobs, device_report):
    device_report("1", {"switch": m.OFF}, m.ON)  # reverse edge cancels the pending
    assert ctx.scheduler.cancel.called


def test_sleeping_rule_schedules_nothing(ctx, react_rules, device_report):
    plugins.sleep(ctx, "Lights off")
    device_report("1", {"switch": m.ON}, m.OFF)
    ctx.scheduler.once.assert_not_called()


def test_sleep_cancels_a_pending_job(ctx, pending_jobs):
    plugins.sleep(ctx, "Lights off")
    assert ctx.scheduler.cancel.called


def test_unwatched_device_is_ignored(ctx, react_rules, device_report):
    device_report("99", {"switch": m.ON}, m.OFF)
    ctx.scheduler.once.assert_not_called()
    ctx.api.dispatch.assert_not_called()


def test_run_react_dispatches_the_action(ctx, ruleset, dispatches, deferred_run, device_report):
    ruleset(_make(m.Devices(Light.lamp), "switch", m.ON, m.OFF, delay=10), {"1": Light.lamp})
    device_report("1", {"switch": m.ON}, m.OFF)
    deferred_run()
    assert dispatches() == [(Light.lamp, m.OFF)]


def test_consecutive_fires_log_the_same_trigger_id(ctx, ruleset, device_report):
    ruleset(_make(m.Devices(Light.lamp), "switch", m.ON, m.OFF), {"1": Light.lamp})
    device_report("1", {"switch": m.ON}, m.OFF)
    ctx.frozen.move_to(NOW + timedelta(seconds=model.COOLDOWN.total_seconds() + 1))
    device_report("1", {"switch": m.ON}, m.OFF)
    assert [call.args[2] for call in ctx.api.log.call_args_list] == [_hub("1"), _hub("1")]


def test_a_different_device_logs_a_different_trigger_id(ctx, ruleset, device_report):
    ruleset(
        [
            *_make(m.Devices(Light.lamp), "switch", m.ON, m.OFF),
            *_make(m.Devices(Light.desk), "switch", m.ON, m.OFF),
        ],
        {"1": Light.lamp, "2": Light.desk},
    )
    device_report("1", {"switch": m.ON}, m.OFF)
    device_report("2", {"switch": m.ON}, m.OFF)
    assert [call.args[2] for call in ctx.api.log.call_args_list] == [_hub("1"), _hub("2")]


def test_untargeted_ac_command_targets_the_ac_set(ctx, react_rules):
    rules = react_rules
    assert rules[4].rule.steps[0].command.subject == m.Devices(Ac)


def test_targeted_action_goes_to_the_target(ctx, ruleset, dispatches, device_report):
    ruleset(_make(m.Devices(Light.lamp), "switch", m.ON, m.OFF, target=m.Devices(Light.desk)), {"1": Light.lamp})
    device_report("1", {"switch": m.ON}, m.OFF)
    assert dispatches() == [(Light.desk, m.OFF)]


def test_contact_open_triggers_immediate_rule(ctx, ruleset, dispatches):
    rule = _make(m.Devices(Light.lamp), "contact", "open", m.AcState(m.ON, m.AcMode.FAN_ONLY, "low", temperature=75), target=m.Devices(Ac))
    ruleset(rule, {"1": Light.lamp})
    state = m.DeviceState(m.Device("1", "balcony door", "hubitat"), {"contact": "open"}, None)
    ctx.api.device_states.return_value = [state]
    plugins._on_event(ctx, ctx.sources, m.Status(state.device, "contact", "closed", "open", hubitat.HubitatSource.HUBITAT))
    assert dispatches() == [(Ac.living, m.AcState(m.ON, m.AcMode.FAN_ONLY, "low", temperature=75))]


def test_if_clause_parses_device_and_condition(ctx, react_rules):
    rules = react_rules
    assert rules[2].rule.steps[0].condition == em.And(model.AcIs(model.AcSubject(Ac.living), m.AcState(power=m.ON)))
    assert rules[3].rule.steps[0].condition == em.And(model.AcIs(model.AcSubject(Ac.living), m.AcState(m.ON, "cool")))


def test_set_clause_parses_explicit_target(ctx, react_rules):
    rules = react_rules
    assert rules[0].rule.steps[0].command.subject == m.Devices(Light.lamp)
    assert rules[2].rule.steps[0].command.subject == m.Devices(Ac.living)


def test_target_must_match_action_kind():
    objects = {"device": SimpleNamespace(enums={"Light": Light, "AC": Ac})}
    with pytest.raises(ValueError):
        react._parse_target("Light.desk", m.AcState(m.ON, m.AcMode.COOL, "low", temperature=75), objects)
    with pytest.raises(ValueError):
        react._parse_target("AC", m.STOP, objects)


def test_if_clause_covers_lights_and_chromecasts(ctx, react_rules):
    rules = react_rules
    assert rules[5].rule.steps[0].condition == em.And(em.Eq(model.MqttDeviceSubject(Light.desk, "switch"), m.ON))
    assert rules[6].rule.steps[0].condition == em.And(em.Eq(model.CastSubject(Chromecast.tv), "playing"))


def test_motion_trigger_with_target_and_no_if_clause(ctx, react_rules):
    # regression: docopt's optional-group matching lets the bracketed `if <device> is
    # <condition>` absorb a stray token even without the literal if/is present, which
    # made a bare `set <target> <action>` (no if step) misparse as `set <action>`
    rules = react_rules
    assert rules[7].condition == model.Transition(model.MqttDeviceSubject(Sensor.living, "motion"), "active")
    assert rules[7].rule.steps[0].condition == em.And()
    assert rules[7].rule.steps[0].command == em.Command(m.Devices(Light.lamp), m.ON)


def test_if_ac_state_gates_immediate_rule(ctx, ruleset, dispatches, ac_report):
    cond = model.AcIs(model.AcSubject(Ac.living), m.AcState(power=m.ON))
    rule = _make(
        m.Devices(Light.lamp), "contact", "open", m.AcState(m.ON, m.AcMode.FAN_ONLY, "low", temperature=75), target=m.Devices(Ac), cond=cond
    )
    ruleset(rule, {"1": Light.lamp})
    state = m.DeviceState(m.Device("1", "balcony door", "hubitat"), {"contact": "open"}, None)
    ctx.api.device_states.return_value = [state]
    ac_report(m.AcState(power=m.OFF))
    plugins._on_event(ctx, ctx.sources, m.Status(state.device, "contact", "closed", "open", hubitat.HubitatSource.HUBITAT))
    ctx.api.dispatch.assert_not_called()
    ctx.api.log.assert_not_called()
    ac_report(m.AcState(m.ON, "cool"))
    plugins._on_event(ctx, ctx.sources, m.Status(state.device, "contact", "closed", "open", hubitat.HubitatSource.HUBITAT))
    assert dispatches() == [(Ac.living, m.AcState(m.ON, m.AcMode.FAN_ONLY, "low", temperature=75))]


@pytest.mark.parametrize(
    "ac_state, fires", [(m.AcState(m.ON, "cool"), True), (m.AcState(m.ON, "fan_only"), False), (m.AcState(power=m.ON), True)]
)
def test_if_ac_is_cool_gates_the_delayed_rule(ctx, ac_state, fires, ruleset, deferred_run, device_report, ac_report):
    cond = model.AcIs(model.AcSubject(Ac.living), m.AcState(m.ON, "cool"))
    ruleset(_make(m.Devices(Light.lamp), "switch", m.ON, m.OFF, delay=10, cond=cond), {"1": Light.lamp})
    ac_report(ac_state)
    device_report("1", {"switch": m.ON}, m.OFF)
    deferred_run()
    assert ctx.api.dispatch.called is fires


@pytest.mark.parametrize("playback, fires", [(m.Playback.PLAYING, True), (m.Playback.STOPPED, False)])
def test_if_chromecast_is_playing_gates_the_delayed_rule(ctx, playback, fires, ruleset, deferred_run, device_report):
    cond = em.Eq(model.CastSubject(Chromecast.tv), "playing")
    ruleset(_make(m.Devices(Light.lamp), "switch", m.ON, m.OFF, delay=10, cond=cond), {"1": Light.lamp})
    ctx.api.capture_sounds.return_value = (m.SoundState(Chromecast.tv, None, 30, playback),)
    device_report("1", {"switch": m.ON}, m.OFF)
    deferred_run()
    assert ctx.api.dispatch.called is fires


@pytest.mark.parametrize("desk, fires", [(m.ON, True), (m.OFF, False)])
def test_if_light_is_on_gates_the_delayed_rule(ctx, desk, fires, ruleset, deferred_run, device_report):
    cond = em.Eq(model.MqttDeviceSubject(Light.desk, "switch"), m.ON)
    ruleset(_make(m.Devices(Light.lamp), "switch", m.ON, m.OFF, delay=10, cond=cond), {"1": Light.lamp})
    ctx.api.device_states.return_value = [m.DeviceState(m.Device("2", "desk", "hubitat"), {"switch": desk}, None)]
    device_report("1", {"switch": m.ON}, m.OFF)
    deferred_run()
    assert ctx.api.dispatch.called is fires


def test_reader_resolves_ac_playback_and_attr(ctx, ac_report):
    ac_report(m.AcState(m.ON, "cool"))
    ctx.api.capture_sounds.return_value = (m.SoundState(Chromecast.tv, "s", 30, m.Playback.PLAYING),)
    ctx.api.device_states.return_value = [m.DeviceState(m.Device("2", "desk", "hubitat"), {"switch": m.ON}, None)]
    read = plugins._reader(ctx)
    assert read(model.AcSubject(Ac.living)) == m.AcState(m.ON, "cool")
    assert read(model.CastSubject(Chromecast.tv)) == "playing"
    assert read(model.MqttDeviceSubject(Light.desk, "switch")) == m.ON


def test_reader_calls_a_function(ctx):
    ctx.api.device_states.return_value = [m.DeviceState(m.Device("5", "sensor", "hubitat"), {"temperature": 77, "humidity": 60}, None)]
    read = plugins._reader(ctx)
    assert read(_dewpoint_of(Sensor.living)) == pytest.approx(62.1, abs=0.2)
    ctx.api.device_states.return_value = [m.DeviceState(m.Device("5", "sensor", "hubitat"), {"humidity": 60}, None)]
    with pytest.raises(KeyError, match="temperature"):
        read(_dewpoint_of(Sensor.living))


@pytest.mark.parametrize("value, inside", [(65.9, False), (66, True), (119.9, True), (120, False)])
def test_range_is_half_open(value, inside):
    assert model.Range(m.OutsideTemperatureSubject(), 66, 120).holds(engine.Runtime(UTC).world(lambda subject: value)) is inside


def test_ac_is():
    cool = model.AcIs(model.AcSubject(Ac.living), m.AcState(m.ON, "cool"))
    assert cool.holds(engine.Runtime(UTC).world(lambda subject: m.AcState(m.ON, "cool", "low", 72)))
    assert not cool.holds(engine.Runtime(UTC).world(lambda subject: m.AcState(m.ON, "fan_only", "low", 72)))
    assert not cool.holds(engine.Runtime(UTC).world(lambda subject: None))


def test_ac_turns_parses(ctx):
    ctx.config.plugin_configs = plugin_config(react, "react_ac")
    rules = react.setup(ctx)
    state = model.MqttDeviceSubject(Ac.living, "state")
    assert rules[0].condition == model.Transition(state, m.AcState(power=m.ON))
    assert rules[0].cancel == em.Changed(state)
    assert rules[0].rule.steps[0].condition == em.And(em.Eq(model.MqttDeviceSubject(Sensor.living, "contact"), "open"))
    assert rules[0].rule.steps[0].command == em.Command(m.Devices(Ac), m.AcState(m.ON, m.AcMode.FAN_ONLY, "low"))
    assert rules[1].condition == model.Transition(state, m.AcState(m.ON, m.AcMode.FAN_ONLY, "low"))
    assert rules[1].rule.steps[0].condition == em.And(model.AcIs(model.AcSubject(Ac.living), m.AcState(m.ON, "cool", "low", 72)))


def test_ac_turns_on_fires_once(ctx, ruleset, dispatches):
    rule = _make(m.Devices(Ac.living), "state", m.AcState(power=m.ON), m.AcState(m.ON, m.AcMode.FAN_ONLY, "low"), target=m.Devices(Ac))
    ruleset(rule, {"clip-1": Ac.living})
    device = m.Device("clip-1", "living", "lg_ac")
    plugins._on_event(ctx, ctx.sources, m.Status(device, "state", m.AcState(power=m.OFF), m.AcState(m.ON, "cool", "low", 72), "lg_ac"))
    assert dispatches() == [(Ac.living, m.AcState(m.ON, m.AcMode.FAN_ONLY, "low"))]
    ctx.api.dispatch.reset_mock()
    plugins._on_event(
        ctx, ctx.sources, m.Status(device, "state", m.AcState(m.ON, "cool", "low", 72), m.AcState(m.ON, "cool", "low", 74), "lg_ac")
    )
    ctx.api.dispatch.assert_not_called()


def test_range_rule_parses_expressions(ctx):
    ctx.config.plugin_configs = plugin_config(react, "react_range")
    rules = react.setup(ctx)
    # A bare attribute reads the device directly; only a named formula needs evaluating.
    temp = model.MqttDeviceSubject(Sensor.living, "temperature")
    dewpoint = _dewpoint_of(Sensor.living)
    assert rules[0].condition == em.Changed(model.Device(Sensor.living))
    assert rules[0].rule.steps[0].condition == em.And(model.Range(temp, 68, 75, edge=True))
    assert rules[0].rule.steps[0].command == em.Command(m.Devices(Ac), m.AcState(m.ON, m.AcMode.COOL, "low", temperature=72))
    assert rules[1].rule.steps[0].condition == em.And(em.And(model.Present(("alice", "bob")), model.Range(dewpoint, 50, 60, edge=True)))
    assert rules[2].rule.steps[0].condition == em.And(em.And(em.Eq(m.AnyoneSubject(), True), model.Range(dewpoint, 59, 104, edge=True)))
    assert rules[3].rule.steps[0].condition == em.And(
        em.And(model.AcIs(model.AcSubject(Ac.living), m.AcState(power=m.ON)), model.Range(dewpoint, 0, 55, edge=True))
    )
    assert rules[4].rule.steps[0].condition == em.And(
        em.And(model.Range(temp, 75, 100, edge=True), model.Range(m.OutsideTemperatureSubject(), 66, 120))
    )


@pytest.mark.parametrize("temperature, fires", [(70, True), (80, False)])
def test_temperature_entering_range_sets_the_ac(ctx, temperature, fires, ruleset, device_report):
    ac = m.AcState(m.ON, m.AcMode.COOL, "low", temperature=72)
    ruleset(
        _make_range(Sensor.living, model.MqttDeviceSubject(Sensor.living, "temperature"), 68, 75, m.Devices(Ac), ac), {"5": Sensor.living}
    )
    device_report(Sensor.living.value, {"temperature": temperature})
    assert ctx.api.dispatch.called is fires


def test_range_fires_only_on_entry(ctx, ruleset, dispatches, device_report):
    ac = m.AcState(m.ON, m.AcMode.COOL, "low", temperature=72)
    ruleset(
        _make_range(Sensor.living, model.MqttDeviceSubject(Sensor.living, "temperature"), 68, 75, m.Devices(Ac), ac), {"5": Sensor.living}
    )
    device_report(Sensor.living.value, {"temperature": 70})
    assert dispatches() == [(Ac.living, ac)]  # AC comes on

    ctx.api.dispatch.reset_mock()
    ctx.frozen.move_to(_past_cooldown(1))
    device_report(Sensor.living.value, {"temperature": 71})
    ctx.api.dispatch.assert_not_called()

    ctx.frozen.move_to(_past_cooldown(2))
    device_report(Sensor.living.value, {"temperature": 80})  # genuinely leaves the range
    ctx.api.dispatch.assert_not_called()

    ctx.frozen.move_to(_past_cooldown(3))
    device_report(Sensor.living.value, {"temperature": 71})  # and re-enters
    assert dispatches() == [(Ac.living, ac)]


@pytest.mark.parametrize("humidity, fires", [(60, True), (20, False)])
def test_dewpoint_formula_in_range_sets_the_ac(ctx, humidity, fires, ruleset, device_report):
    ac = m.AcState(m.ON, m.AcMode.FAN_ONLY, "low", temperature=70)
    ruleset(_make_range(Sensor.living, _dewpoint_of(Sensor.living), 59, 64, m.Devices(Ac), ac), {"5": Sensor.living})
    device_report(Sensor.living.value, {"temperature": 77, "humidity": humidity})
    assert ctx.api.dispatch.called is fires


@pytest.mark.parametrize(
    "people, home, fires",
    [(("alice", "bob"), set(), False), (("alice", "bob"), {"alice"}, True), (m.Anyone(), set(), False), (m.Anyone(), {"bob"}, True)],
)
def test_presence_gates_the_range_rule(ctx, people, home, fires, ruleset, device_report):
    ac = m.AcState(m.ON, m.AcMode.COOL, "low", temperature=72)
    ruleset(
        _make_range(Sensor.living, model.MqttDeviceSubject(Sensor.living, "temperature"), 68, 75, m.Devices(Ac), ac, people=people),
        {"5": Sensor.living},
    )
    ctx.api.present_names.return_value = home
    device_report(Sensor.living.value, {"temperature": 70})
    assert ctx.api.dispatch.called is fires


def test_each_line_keeps_its_pause(ctx, react_rules):
    groups = ctx.plugin_state[react].groups
    assert groups["Lights off"] == model.Group((react_rules[0], react_rules[1]), timedelta(minutes=10))
    assert groups["Desk cools"].pause == timedelta(minutes=30)


def test_disabled_rule_expires_after_its_pause(react_rules):
    state = model.State((), {"Lights off": model.Group((react_rules[0],), timedelta(minutes=10))}, {react_rules[0]: "Lights off"})
    state.disabled["Lights off"] = NOW + timedelta(minutes=10)
    assert plugins.is_disabled(state, react_rules[0], NOW + timedelta(minutes=9))
    assert not plugins.is_disabled(state, react_rules[0], NOW + timedelta(minutes=10))
    assert not state.disabled


def test_disabled_rule_does_not_fire(ctx, react_rules, device_report):
    plugins.sleep(ctx, "Lights off")
    device_report(react_rules[0].condition.subject.device.value, {"switch": m.ON}, m.OFF)
    ctx.api.dispatch.assert_not_called()


def test_wake_clears_a_sleeping_rule(ctx, react_rules):
    state = ctx.plugin_state[react]
    until = plugins.sleep(ctx, "Lights off")
    assert until == NOW + timedelta(minutes=10)
    assert plugins.disabled_until(state, "Lights off", NOW) == until
    plugins.wake(ctx, "Lights off")
    assert not plugins.is_disabled(state, react_rules[0], NOW)
    assert plugins.disabled_until(state, "Lights off", NOW) is None


@pytest.fixture
def flask_client(ctx, react_rules):
    app = Flask(__name__)
    app.orc = ctx  # type: ignore[attr-defined]
    app.register_blueprint(web.react_bp)
    return app.test_client()


def test_rules_endpoint_groups_rules_by_name(flask_client, react_rules):
    body = flask_client.get("/").get_json()
    assert [r["name"] for r in body["rules"]] == [
        "Lights off",
        "Desk cools",
        "Desk stops AC",
        "Lamp cools",
        "Lamp off with desk",
        "Lamp off while playing",
        "Motion lamp",
    ]
    assert all(r["sleeping_until"] is None for r in body["rules"])


def test_sleep_endpoint_disables_every_rule_of_the_name_and_logs(flask_client, ctx, react_rules):
    response = flask_client.get("/Lights%20off/sleep?sleeping=1")
    until = NOW + timedelta(minutes=10)
    assert (response.status_code, response.get_json()) == (200, {"sleeping_until": until.isoformat()})
    state = ctx.plugin_state[react]
    assert plugins.is_disabled(state, react_rules[0], NOW) and plugins.is_disabled(state, react_rules[1], NOW)
    assert not plugins.is_disabled(state, react_rules[2], NOW)
    assert flask_client.get("/").get_json()["rules"][0]["sleeping_until"] == until.isoformat()
    assert ctx.api.log.call_args.args[1] == "`Lights off` sleeping until 15:10"


def test_wake_endpoint_enables_the_rule_and_logs(flask_client, ctx, react_rules):
    ctx.plugin_state[react].disabled["Desk stops AC"] = NOW + timedelta(minutes=10)
    assert flask_client.get("/Desk%20stops%20AC/sleep?sleeping=0").get_json() == {"sleeping_until": None}
    assert not plugins.is_disabled(ctx.plugin_state[react], react_rules[3], NOW)
    assert ctx.api.log.call_args.args[1] == "`Desk stops AC` awake"


def test_unknown_rule_is_a_404(flask_client):
    assert flask_client.get("/Nope/sleep?sleeping=1").status_code == 404


@pytest.mark.parametrize("case, error, message", _PAUSE_ERRORS, ids=[case for case, _, _ in _PAUSE_ERRORS])
def test_bad_pause_is_rejected(ctx, case, error, message):
    ctx.config.plugin_configs = plugin_config(react, case)
    with pytest.raises(error, match=message):
        react.setup(ctx)


def test_function_arguments_resolve(ctx):
    namespace = react.build_namespace(Sensor.living, DEVICE_ENUMS)
    dewpoint = namespace["dewpoint"]
    assert namespace["self"] == m.Devices(Sensor.living)
    assert dewpoint(namespace["Sensor"].living, "temperature", "humidity").args[0] == m.Devices(Sensor.living)
    assert dewpoint(namespace["Sensor"], "temperature", "humidity").args[0] == m.Devices(Sensor)
    with pytest.raises(ValueError, match="attribute, not a device"):
        dewpoint(namespace["temperature"], "humidity")


def test_phrases_expand_to_the_same_condition(ctx):
    ctx.config.plugin_configs = plugin_config(react, "react_phrase")
    phrased, inline = react.setup(ctx)
    assert phrased.rule.steps[0].condition == inline.rule.steps[0].condition


def test_phrase_name_cannot_repeat(ctx):
    ctx.config.plugin_configs = plugin_config(react, "react_phrase_duplicate")
    with pytest.raises(ConfigError, match="already taken"):
        react.setup(ctx)


def _world_read(mock):
    def read(subject):
        match subject:
            case m.PersonSubject(name):
                return name in mock.api.present_names()
            case m.AnyoneSubject():
                return bool(mock.api.present_names())
            case _:
                raise KeyError(subject)

    return read


def _make(devices, attribute, state, action, target=None, delay=None, cond=None):
    cond = cond or em.And()
    span = timedelta(minutes=delay) if delay else timedelta()
    watches = []
    for source in devices.all():
        command = em.Command(target or m.Devices(source), action)
        trigger = model.Transition(model.MqttDeviceSubject(source, attribute), state)
        watches.append(em.Watch(trigger, em.Rule((em.Step(cond, command),)), span, model.COOLDOWN))
    return watches


def _hub(id):
    return m.Broker(id=id, source="hubitat")


def _payload(call):
    """call.args is (func, when, *payload) — skip the scheduling positionals."""
    return call.args[2:]


def _dewpoint_of(sensor):
    return model.FormulaSubject(react.FUNCTIONS["dewpoint"], (m.Devices(sensor), "temperature", "humidity"))


def _make_range(sensor, subject, low, high, target, action, people=None):
    watched = []
    if isinstance(people, m.Anyone):
        watched.append(em.Eq(m.AnyoneSubject(), True))
    elif people:
        watched.append(model.Present(people))
    watched.append(model.Range(subject, low, high, edge=True))
    command = em.Command(target, action)
    rule = em.Rule((em.Step(em.And(*watched), command),))
    return [em.Watch(em.Changed(model.Device(sensor)), rule, cooldown=model.COOLDOWN)]


def _past_cooldown(steps):
    return NOW + timedelta(seconds=steps * (model.COOLDOWN.total_seconds() + 1))
