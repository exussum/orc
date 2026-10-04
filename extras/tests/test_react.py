from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from command_cfg import ConfigError
from flask import Flask
from orc_engine import engine
from orc_extras import react
from orc_extras.react import model, plugins, web

import orc
from orc import model as m
from orc.model import DeviceEnum

FIXTURE = Path(__file__).parent / "fixture"
_UTC = ZoneInfo("UTC")
_NOW = datetime(2024, 1, 1, 15, tzinfo=_UTC)


class Light(DeviceEnum):
    lamp = 1
    desk = 2


class Ac(DeviceEnum):
    living = "clip-1"


class Chromecast(DeviceEnum):
    tv = "host9"


class Sensor(DeviceEnum):
    living = 5


@pytest.fixture(autouse=True)
def _device_enums(monkeypatch):
    from orc.kernel import declarations

    monkeypatch.setattr(
        orc.config, "registry", declarations.Declarations().build({"Light": Light, "AC": Ac, "Chromecast": Chromecast, "Sensor": Sensor})
    )


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


@pytest.fixture
def ctx(ctx):
    ctx.engine = engine.Runtime(lambda _subject: ctx.api.local_now())
    ctx.api.local_now.return_value = _NOW
    ctx.api.world_reader.return_value = _world_read(ctx)
    ctx.api.squish.side_effect = lambda commands, entry: m.squish(commands)
    ctx.api.capture_acs.return_value = (m.AcStatus(Ac.living, m.AcState.OFF),)
    ctx.config.settings.tz = _UTC
    ctx.config.registry = orc.config.registry
    ctx.plugin_state = {react: model.State((), {}, {})}
    return ctx


@pytest.fixture
def configured(ctx):
    ctx.config.plugin_configs = {react.CONFIG: (FIXTURE / "react.orc").read_text()}
    rules = react.setup(ctx)
    ctx.sources = ctx.api.add_listener.call_args.args[0].args[1]
    return rules


def _make(devices, attribute, state, action, target=None, delay=None, when=None):
    cond = model.condition(when)
    span = timedelta(minutes=delay) if delay else timedelta()
    automations = []
    for source in devices.all():
        command = engine.Command(target or m.Devices(source), action)
        trigger = model.Transition(m.MqttDeviceSubject(source, attribute), state)
        automations.append(engine.Automation(trigger, engine.Rule((engine.Step(cond, command),)), span, model.COOLDOWN))
    return automations


def _hub(id):
    return m.Broker(id=id, source="hubitat")


@pytest.fixture
def ruleset(ctx):
    def ruleset(automations, sources):
        ctx.plugin_state[react].automations = tuple(automations)
        ctx.sources = sources

    return ruleset


@pytest.fixture
def switch_report(ctx):
    def switch_report(device_id, old, new):
        device = m.DeviceState(id=device_id, name="lamp", attributes={"switch": new}, last_activity=None)
        plugins._on_event(ctx, ctx.sources, device, "switch", old, new)

    return switch_report


@pytest.fixture
def ac_report(ctx):
    def ac_report(state):
        ctx.api.capture_acs.return_value = (m.AcStatus(Ac.living, state),)

    return ac_report


@pytest.fixture
def dispatches(ctx):
    return lambda: [(c.subject.one(), c.value) for c in ctx.api.dispatch.call_args.args[0]]


def _payload(call):
    """call.args is (func, when, *payload) — skip the scheduling positionals."""
    return call.args[2:]


@pytest.fixture
def deferred_run(ctx):
    def deferred_run():
        deferred, name = _payload(ctx.scheduler.once.call_args)
        plugins._run_react.__wrapped__(deferred, name, ctx=ctx)

    return deferred_run


# The fixture's first line (`react Light ...`) fans out to lamp + desk, so the
# compiled rules are: 0 lamp/on, 1 desk/on, 2..7 the single-device lines 2..7.
def test_config_registers_listener(ctx, configured):
    rules = configured
    assert len(rules) == 8  # line 1 fans out to lamp + desk; lines 2..7 are single-device
    assert rules[0].trigger == model.Transition(m.MqttDeviceSubject(Light.lamp, "switch"), m.ON)
    assert rules[1].trigger == model.Transition(m.MqttDeviceSubject(Light.desk, "switch"), m.ON)
    assert rules[0].rule.steps[0].command == engine.Command(m.Devices(Light.lamp), m.OFF)
    assert rules[0].delay == timedelta(minutes=10)
    assert ctx.api.add_listener.call_args.args[0].args[1] == {1: Light.lamp, 2: Light.desk, 5: Sensor.living}


def test_switch_on_schedules_reaction(ctx, configured, switch_report):
    switch_report(1, m.OFF, m.ON)
    call = ctx.scheduler.once.call_args
    assert call.args[0] is plugins._run_react
    assert call.kwargs["id"].startswith("react-")
    assert _payload(call)[1] == "lamp"


def test_switch_already_on_schedules_nothing(ctx, configured, switch_report):
    switch_report(1, m.ON, m.ON)
    ctx.scheduler.once.assert_not_called()


def test_switch_off_cancels_pending_jobs(ctx, configured, switch_report):
    switch_report(1, m.OFF, m.ON)  # rule 0 has --delay, so it goes pending
    ctx.scheduler.reset_mock()
    switch_report(1, m.ON, m.OFF)  # reverse edge cancels the pending
    assert ctx.scheduler.cancel.called


def test_sleeping_rule_schedules_nothing(ctx, configured, switch_report):
    plugins.sleep(ctx, "Lights off")
    switch_report(1, m.OFF, m.ON)
    ctx.scheduler.once.assert_not_called()


def test_sleep_cancels_a_pending_job(ctx, configured, switch_report):
    switch_report(1, m.OFF, m.ON)
    ctx.scheduler.reset_mock()
    plugins.sleep(ctx, "Lights off")
    assert ctx.scheduler.cancel.called


def test_unwatched_device_is_ignored(ctx, configured, switch_report):
    switch_report(99, m.OFF, m.ON)
    ctx.scheduler.once.assert_not_called()
    ctx.api.dispatch.assert_not_called()


def test_run_react_dispatches_the_action(ctx, ruleset, dispatches, deferred_run, switch_report):
    ruleset(_make(m.Devices(Light.lamp), "switch", m.ON, m.OFF, delay=10), {1: Light.lamp})
    switch_report(1, m.OFF, m.ON)
    deferred_run()
    assert dispatches() == [(Light.lamp, m.OFF)]


def test_consecutive_fires_log_the_same_trigger_id(ctx, ruleset, switch_report):
    ruleset(_make(m.Devices(Light.lamp), "switch", m.ON, m.OFF), {1: Light.lamp})
    switch_report(1, m.OFF, m.ON)
    ctx.api.local_now.return_value = _NOW + timedelta(seconds=model.COOLDOWN.total_seconds() + 1)
    switch_report(1, m.OFF, m.ON)
    assert [call.args[2] for call in ctx.api.log.call_args_list] == [_hub("1"), _hub("1")]


def test_a_different_device_logs_a_different_trigger_id(ctx, ruleset, switch_report):
    ruleset(
        [
            *_make(m.Devices(Light.lamp), "switch", m.ON, m.OFF),
            *_make(m.Devices(Light.desk), "switch", m.ON, m.OFF),
        ],
        {1: Light.lamp, 2: Light.desk},
    )
    switch_report(1, m.OFF, m.ON)
    switch_report(2, m.OFF, m.ON)
    assert [call.args[2] for call in ctx.api.log.call_args_list] == [_hub("1"), _hub("2")]


def test_untargeted_ac_command_targets_the_ac_set(ctx, configured):
    rules = configured
    assert rules[4].rule.steps[0].command.subject == m.Devices(Ac)


def test_targeted_action_goes_to_the_target(ctx, ruleset, dispatches, switch_report):
    ruleset(_make(m.Devices(Light.lamp), "switch", m.ON, m.OFF, target=m.Devices(Light.desk)), {1: Light.lamp})
    switch_report(1, m.OFF, m.ON)
    assert dispatches() == [(Light.desk, m.OFF)]


def test_contact_open_triggers_immediate_rule(ctx, ruleset, dispatches):
    rule = _make(m.Devices(Light.lamp), "contact", "open", m.AcCommand(m.AcMode.FAN_ONLY, "low", 75), target=m.Devices(Ac))
    ruleset(rule, {56: Light.lamp})
    device = m.DeviceState(id=56, name="balcony door", attributes={"contact": "open"}, last_activity=None)
    plugins._on_event(ctx, ctx.sources, device, "contact", "closed", "open")
    assert dispatches() == [(Ac.living, m.AcCommand(m.AcMode.FAN_ONLY, "low", 75))]


def test_if_clause_parses_device_and_condition(ctx, configured):
    rules = configured
    assert rules[2].rule.steps[0].conditions[0] == model.AcIs(m.AcSubject(Ac.living), m.AcState.ON)
    assert rules[3].rule.steps[0].conditions[0] == model.AcIs(m.AcSubject(Ac.living), m.AcState.COOL)


def test_set_clause_parses_explicit_target(ctx, configured):
    rules = configured
    assert rules[0].rule.steps[0].command.subject == m.Devices(Light.lamp)
    assert rules[2].rule.steps[0].command.subject == m.Devices(Ac.living)


def test_target_must_match_action_kind():
    objects = {"device": SimpleNamespace(enums={"Light": Light, "AC": Ac})}
    with pytest.raises(ValueError):
        react._parse_target("Light.desk", m.AcCommand(m.AcMode.COOL, "low", 75), objects)
    with pytest.raises(ValueError):
        react._parse_target("AC", m.STOP, objects)


def test_if_clause_covers_lights_and_chromecasts(ctx, configured):
    rules = configured
    assert rules[5].rule.steps[0].conditions[0] == engine.Eq(m.MqttDeviceSubject(Light.desk, "switch"), m.ON)
    assert rules[6].rule.steps[0].conditions[0] == engine.Eq(m.CastSubject(Chromecast.tv), m.Playback.PLAYING)


def test_motion_trigger_with_target_and_no_if_clause(ctx, configured):
    # regression: docopt's optional-group matching lets the bracketed `if <device> is
    # <condition>` absorb a stray token even without the literal if/is present, which
    # made a bare `set <target> <action>` (no if step) misparse as `set <action>`
    rules = configured
    assert rules[7].trigger == model.Transition(m.MqttDeviceSubject(Sensor.living, "motion"), "active")
    assert rules[7].rule.steps[0].conditions == ()
    assert rules[7].rule.steps[0].command == engine.Command(m.Devices(Light.lamp), m.ON)


def test_when_requires_a_known_condition():
    objects = {"device": SimpleNamespace(enums={"Light": Light, "AC": Ac, "Chromecast": Chromecast})}
    with pytest.raises(ValueError):
        react._parse_when(Ac.living, "heat", objects)
    with pytest.raises(ValueError):
        react._parse_when(Light.desk, "cool", objects)
    with pytest.raises(ValueError):
        react._parse_when(Chromecast.tv, "on", objects)


def test_when_gates_immediate_rule_on_ac_state(ctx, ruleset, dispatches, ac_report):
    when = model.When(Ac.living, m.AcState.ON)
    rule = _make(m.Devices(Light.lamp), "contact", "open", m.AcCommand(m.AcMode.FAN_ONLY, "low", 75), target=m.Devices(Ac), when=when)
    ruleset(rule, {56: Light.lamp})
    device = m.DeviceState(id=56, name="balcony door", attributes={"contact": "open"}, last_activity=None)
    ac_report(m.AcState.OFF)
    plugins._on_event(ctx, ctx.sources, device, "contact", "closed", "open")
    ctx.api.dispatch.assert_not_called()
    ctx.api.log.assert_not_called()
    ac_report(m.AcState.COOL)
    plugins._on_event(ctx, ctx.sources, device, "contact", "closed", "open")
    assert dispatches() == [(Ac.living, m.AcCommand(m.AcMode.FAN_ONLY, "low", 75))]


@pytest.mark.parametrize("ac_state, fires", [(m.AcState.COOL, True), (m.AcState.FAN_ONLY, False), (m.AcState.ON, False)])
def test_if_ac_is_cool_gates_the_delayed_rule(ctx, ac_state, fires, ruleset, deferred_run, switch_report, ac_report):
    when = model.When(Ac.living, m.AcState.COOL)
    ruleset(_make(m.Devices(Light.lamp), "switch", m.ON, m.OFF, delay=10, when=when), {1: Light.lamp})
    ac_report(ac_state)
    switch_report(1, m.OFF, m.ON)
    deferred_run()
    assert ctx.api.dispatch.called is fires


@pytest.mark.parametrize("playback, fires", [(m.Playback.PLAYING, True), (m.Playback.STOPPED, False)])
def test_if_chromecast_is_playing_gates_the_delayed_rule(ctx, playback, fires, ruleset, deferred_run, switch_report):
    when = model.When(Chromecast.tv, m.Playback.PLAYING)
    ruleset(_make(m.Devices(Light.lamp), "switch", m.ON, m.OFF, delay=10, when=when), {1: Light.lamp})
    ctx.api.capture_sounds.return_value = (m.SoundState(Chromecast.tv, None, 30, playback),)
    switch_report(1, m.OFF, m.ON)
    deferred_run()
    assert ctx.api.dispatch.called is fires


@pytest.mark.parametrize("desk, fires", [(m.ON, True), (m.OFF, False)])
def test_if_light_is_on_gates_the_delayed_rule(ctx, desk, fires, ruleset, deferred_run, switch_report):
    when = model.When(Light.desk, m.ON)
    ruleset(_make(m.Devices(Light.lamp), "switch", m.ON, m.OFF, delay=10, when=when), {1: Light.lamp})
    ctx.api.device_states.return_value = [m.DeviceState(id=2, name="desk", attributes={"switch": desk}, last_activity=None)]
    switch_report(1, m.OFF, m.ON)
    deferred_run()
    assert ctx.api.dispatch.called is fires


def test_reader_resolves_ac_playback_and_attr(ctx, ac_report):
    ac_report(m.AcState.COOL)
    ctx.api.capture_sounds.return_value = (m.SoundState(Chromecast.tv, "s", 30, m.Playback.PLAYING),)
    ctx.api.device_states.return_value = [m.DeviceState(id=2, name="desk", attributes={"switch": m.ON}, last_activity=None)]
    read = plugins._reader(ctx)
    assert read(m.AcSubject(Ac.living)) == m.AcState.COOL
    assert read(m.CastSubject(Chromecast.tv)) == m.Playback.PLAYING
    assert read(m.MqttDeviceSubject(Light.desk, "switch")) == m.ON


def test_reader_formula_evaluates_and_raises_with_context(ctx):
    ctx.api.device_states.return_value = [
        m.DeviceState(id=5, name="sensor", attributes={"temperature": 77, "humidity": 60}, last_activity=None)
    ]
    read = plugins._reader(ctx)
    assert read(model.FormulaSubject(Sensor.living, "dewpoint(temperature,humidity)")) == pytest.approx(62.1, abs=0.2)
    ctx.api.device_states.return_value = [m.DeviceState(id=5, name="sensor", attributes={"humidity": 60}, last_activity=None)]
    with pytest.raises(ValueError, match="dewpoint"):
        read(model.FormulaSubject(Sensor.living, "dewpoint(temperature,humidity)"))


def test_condition_maps_when_by_kind():
    assert model.condition(None) == ()
    assert model.condition(model.When(Ac.living, m.AcState.ON)) == (model.AcIs(m.AcSubject(Ac.living), m.AcState.ON),)
    assert model.condition(model.When(Chromecast.tv, m.Playback.PLAYING)) == (engine.Eq(m.CastSubject(Chromecast.tv), m.Playback.PLAYING),)
    assert model.condition(model.When(Light.desk, m.ON)) == (engine.Eq(m.MqttDeviceSubject(Light.desk, "switch"), m.ON),)


def test_ac_is_bitmask_respects_flag_membership():
    assert not model.AcIs(m.AcSubject(Ac.living), m.AcState.COOL).holds(lambda subject: m.AcState.ON)
    assert model.AcIs(m.AcSubject(Ac.living), m.AcState.ON).holds(lambda subject: m.AcState.COOL)
    assert not model.AcIs(m.AcSubject(Ac.living), m.AcState.COOL).holds(lambda subject: None)


def _make_range(sensor, expr, low, high, target, action, people=None):
    formula = model.FormulaSubject(sensor, expr)
    conditions = []
    if people == m.Tag.ANYONE:
        conditions.append(engine.Eq(m.AnyoneSubject(), True))
    elif people:
        conditions.append(model.Present(people))
    if isinstance(action, m.AcCommand):
        conditions.extend(model.AcIs(m.AcSubject(ac), m.AcState.OFF) for ac in target.all())
    conditions.append(model.Range(formula, low, high))
    command = engine.Command(target, action)
    rule = engine.Rule((engine.Step(tuple(conditions), command),))
    return [engine.Automation(model.DeviceChanged(sensor, expr), rule, cooldown=model.COOLDOWN)]


@pytest.fixture
def range_event(ctx):
    def range_event(sensor, attributes):
        device = m.DeviceState(id=sensor.value, name="sensor", attributes=attributes, last_activity=None)
        ctx.api.device_states.return_value = [device]
        changed = next(iter(attributes))
        plugins._on_event(ctx, ctx.sources, device, changed, None, attributes[changed])

    return range_event


def test_range_rule_parses_expressions(ctx):
    ctx.config.plugin_configs = {react.CONFIG: (FIXTURE / "react_range.orc").read_text()}
    rules = react.setup(ctx)
    temp = model.FormulaSubject(Sensor.living, "temperature")
    dewpoint = model.FormulaSubject(Sensor.living, "dewpoint(temperature,humidity)")
    assert rules[0].trigger == model.DeviceChanged(Sensor.living, "temperature")
    assert rules[0].rule.steps[0].conditions == (model.AcIs(m.AcSubject(Ac.living), m.AcState.OFF), model.Range(temp, 68, 75))
    assert rules[0].rule.steps[0].command == engine.Command(m.Devices(Ac), m.AcCommand(m.AcMode.COOL, "low", 72))
    assert rules[1].trigger == model.DeviceChanged(Sensor.living, "dewpoint(temperature,humidity)")
    assert rules[1].rule.steps[0].conditions == (
        model.Present(("alice", "bob")),
        model.AcIs(m.AcSubject(Ac.living), m.AcState.OFF),
        model.Range(dewpoint, 50, 60),
    )
    assert rules[2].trigger == model.DeviceChanged(Sensor.living, "dewpoint(temperature,humidity)")
    assert rules[2].rule.steps[0].conditions == (engine.Eq(m.AnyoneSubject(), True), model.Range(dewpoint, 59, 104))
    assert rules[3].rule.steps[0].conditions == (model.AcIs(m.AcSubject(Ac.living), m.AcState.ON), model.Range(dewpoint, 0, 55))


@pytest.mark.parametrize(
    "temperature, ac_state, fires", [(70, m.AcState.OFF, True), (70, m.AcState.COOL, False), (80, m.AcState.OFF, False)]
)
def test_temperature_in_range_sets_an_idle_ac(ctx, temperature, ac_state, fires, ruleset, range_event, ac_report):
    ac = m.AcCommand(m.AcMode.COOL, "low", 72)
    ruleset(_make_range(Sensor.living, "temperature", 68, 75, m.Devices(Ac), ac), {5: Sensor.living})
    ac_report(ac_state)
    range_event(Sensor.living, {"temperature": temperature})
    assert ctx.api.dispatch.called is fires


def _past_cooldown(steps):
    return _NOW + timedelta(seconds=steps * (model.COOLDOWN.total_seconds() + 1))


def test_ac_turned_off_manually_does_not_self_correct_until_range_reentered(ctx, ruleset, dispatches, range_event, ac_report):
    ac = m.AcCommand(m.AcMode.COOL, "low", 72)
    ruleset(_make_range(Sensor.living, "temperature", 68, 75, m.Devices(Ac), ac), {5: Sensor.living})
    range_event(Sensor.living, {"temperature": 70})
    assert dispatches() == [(Ac.living, ac)]  # AC comes on

    # someone (or something) turns the AC back off while the temperature is still in range
    ac_report(m.AcState.OFF)
    ctx.api.dispatch.reset_mock()
    ctx.api.local_now.return_value = _past_cooldown(1)
    range_event(Sensor.living, {"temperature": 71})
    ctx.api.dispatch.assert_not_called()  # doesn't self-correct

    ctx.api.local_now.return_value = _past_cooldown(2)
    range_event(Sensor.living, {"temperature": 80})  # genuinely leaves the range
    ctx.api.dispatch.assert_not_called()

    ctx.api.local_now.return_value = _past_cooldown(3)
    range_event(Sensor.living, {"temperature": 71})  # and re-enters
    assert dispatches() == [(Ac.living, ac)]


@pytest.mark.parametrize("humidity, fires", [(60, True), (20, False)])
def test_dewpoint_formula_in_range_sets_the_ac(ctx, humidity, fires, ruleset, range_event):
    ac = m.AcCommand(m.AcMode.FAN_ONLY, "low", 70)
    ruleset(_make_range(Sensor.living, "dewpoint(temperature,humidity)", 59, 64, m.Devices(Ac), ac), {5: Sensor.living})
    range_event(Sensor.living, {"temperature": 77, "humidity": humidity})
    assert ctx.api.dispatch.called is fires


@pytest.mark.parametrize(
    "people, home, fires",
    [(("alice", "bob"), set(), False), (("alice", "bob"), {"alice"}, True), (m.Tag.ANYONE, set(), False), (m.Tag.ANYONE, {"bob"}, True)],
)
def test_presence_gates_the_range_rule(ctx, people, home, fires, ruleset, range_event):
    ac = m.AcCommand(m.AcMode.COOL, "low", 72)
    ruleset(_make_range(Sensor.living, "temperature", 68, 75, m.Devices(Ac), ac, people=people), {5: Sensor.living})
    ctx.api.present_names.return_value = home
    range_event(Sensor.living, {"temperature": 70})
    assert ctx.api.dispatch.called is fires


def test_each_line_keeps_its_pause(ctx, configured):
    groups = ctx.plugin_state[react].groups
    assert groups["Lights off"] == model.Group((configured[0].rule, configured[1].rule), timedelta(minutes=10))
    assert groups["Desk cools"].pause == timedelta(minutes=30)


def test_disabled_rule_expires_after_its_pause(configured):
    state = model.State((), {"Lights off": model.Group((configured[0].rule,), timedelta(minutes=10))}, {configured[0].rule: "Lights off"})
    state.disabled["Lights off"] = _NOW + timedelta(minutes=10)
    assert plugins.is_disabled(state, configured[0], _NOW + timedelta(minutes=9))
    assert not plugins.is_disabled(state, configured[0], _NOW + timedelta(minutes=10))
    assert not state.disabled


def test_disabled_rule_does_not_fire(ctx, configured, switch_report):
    plugins.sleep(ctx, "Lights off")
    switch_report(configured[0].trigger.subject.device.value, m.OFF, m.ON)
    ctx.api.dispatch.assert_not_called()


def test_wake_clears_a_sleeping_rule(ctx, configured):
    state = ctx.plugin_state[react]
    until = plugins.sleep(ctx, "Lights off")
    assert until == _NOW + timedelta(minutes=10)
    assert plugins.disabled_until(state, "Lights off", _NOW) == until
    plugins.wake(ctx, "Lights off")
    assert not plugins.is_disabled(state, configured[0], _NOW)
    assert plugins.disabled_until(state, "Lights off", _NOW) is None


@pytest.fixture
def client(ctx, configured):
    app = Flask(__name__)
    app.orc = ctx  # type: ignore[attr-defined]
    app.register_blueprint(web.react_bp)
    return app.test_client()


def test_rules_endpoint_groups_rules_by_name(client, configured):
    body = client.get("/").get_json()
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


def test_sleep_endpoint_disables_every_rule_of_the_name_and_logs(client, ctx, configured):
    response = client.get("/Lights%20off/sleep?sleeping=1")
    until = _NOW + timedelta(minutes=10)
    assert (response.status_code, response.get_json()) == (200, {"sleeping_until": until.isoformat()})
    state = ctx.plugin_state[react]
    assert plugins.is_disabled(state, configured[0], _NOW) and plugins.is_disabled(state, configured[1], _NOW)
    assert not plugins.is_disabled(state, configured[2], _NOW)
    assert client.get("/").get_json()["rules"][0]["sleeping_until"] == until.isoformat()
    assert ctx.api.log.call_args.args[1] == "`Lights off` sleeping until 15:10"


def test_wake_endpoint_enables_the_rule_and_logs(client, ctx, configured):
    ctx.plugin_state[react].disabled["Desk stops AC"] = _NOW + timedelta(minutes=10)
    assert client.get("/Desk%20stops%20AC/sleep?sleeping=0").get_json() == {"sleeping_until": None}
    assert not plugins.is_disabled(ctx.plugin_state[react], configured[3], _NOW)
    assert ctx.api.log.call_args.args[1] == "`Desk stops AC` awake"


def test_unknown_rule_is_a_404(client):
    assert client.get("/Nope/sleep?sleeping=1").status_code == 404


def test_zero_pause_is_rejected(ctx):
    ctx.config.plugin_configs = {react.CONFIG: (FIXTURE / "react_pause_zero.orc").read_text()}
    with pytest.raises(ConfigError, match="Invalid --pause 0"):
        react.setup(ctx)


def test_a_name_shared_with_a_different_pause_is_rejected(ctx):
    ctx.config.plugin_configs = {react.CONFIG: (FIXTURE / "react_pause_mismatch.orc").read_text()}
    with pytest.raises(ValueError, match="same --pause"):
        react.setup(ctx)
