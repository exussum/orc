from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import create_autospec
from zoneinfo import ZoneInfo

import pytest
from apscheduler.schedulers.base import BaseScheduler
from orc_extras import react
from orc_extras.react import plugins

import orc
from orc import model as m
from orc.kernel import engine
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
    def read(channel):
        match channel:
            case m.PersonChannel(name):
                return name in mock.api.present_names()
            case m.AnyoneChannel():
                return bool(mock.api.present_names())
            case _:
                raise KeyError(channel)

    return read


@pytest.fixture
def ctx(ctx):
    ctx.engine = engine.Runtime([])
    ctx.scheduler = create_autospec(BaseScheduler, instance=True)
    ctx.api.JOBSTORE_MEMORY = "memory"
    ctx.api.local_now.return_value = _NOW
    ctx.api.device_state.side_effect = lambda target: next(
        (s for s in ctx.api.device_states.return_value if str(s.id) == target or s.name == target), None
    )
    ctx.api.world_reader.return_value = _world_read(ctx)
    ctx.api.capture_acs.return_value = (m.AcStatus(Ac.living, m.AcState.OFF),)
    ctx.config.settings.tz = _UTC
    ctx.config.registry = orc.config.registry
    ctx.plugin_state = {}
    return ctx


@pytest.fixture
def configured(ctx):
    ctx.config.plugin_configs = {react.CONFIG: (FIXTURE / "react.orc").read_text()}
    rules = react.setup(ctx)
    return rules, ctx.api.add_listener.call_args.args[0]


def _make(devices, attribute, state, action, target=None, delay=None, when=None):
    cond = plugins.condition(when)
    span = timedelta(minutes=delay) if delay else timedelta()
    rules = []
    for source in devices.all():
        command = engine.Command(target or m.Devices(source), action)
        trigger = engine.Transition(m.MqttDeviceChannel(source, attribute), state)
        rules.append(engine.Rule(trigger, (engine.Clause(cond, command),), span, cooldown=plugins.COOLDOWN))
    return rules


def _hub(id):
    return m.Broker(id=id, source="hubitat")


def _install(ctx, engine_rules, sources):
    ctx.engine.add_rules(engine_rules)
    ctx.sources = sources


def _switch(ctx, listener, device_id, old, new):
    device = m.DeviceState(id=device_id, name="lamp", attributes={"switch": new}, last_activity=None)
    listener.func(*listener.args, device, "switch", old, new)


def _dispatched(ctx):
    return [(c.channel.one(), c.value) for c in ctx.api.dispatch.call_args.args[0]]


def _fire_pending(ctx):
    deferred, name = ctx.scheduler.add_job.call_args.kwargs["args"]
    plugins._run_react.__wrapped__(deferred, name, ctx=ctx)


# The fixture's first line (`react Light ...`) fans out to lamp + desk, so the
# compiled rules are: 0 lamp/on, 1 desk/on, 2..7 the single-device lines 2..7.
def test_config_registers_listener(ctx, configured):
    rules, _ = configured
    assert len(rules) == 8  # line 1 fans out to lamp + desk; lines 2..7 are single-device
    assert rules[0].trigger == engine.Transition(m.MqttDeviceChannel(Light.lamp, "switch"), m.ON)
    assert rules[1].trigger == engine.Transition(m.MqttDeviceChannel(Light.desk, "switch"), m.ON)
    assert rules[0].items[0].command == engine.Command(m.Devices(Light.lamp), m.OFF)
    assert rules[0].delay == timedelta(minutes=10)
    assert ctx.api.add_listener.call_args.args[0].args[1] == {1: Light.lamp, 2: Light.desk, 5: Sensor.living}


def test_switch_on_schedules_reaction(ctx, configured):
    listener = ctx.api.add_listener.call_args.args[0]
    _switch(ctx, listener, 1, m.OFF, m.ON)
    call = ctx.scheduler.add_job.call_args
    assert call.args[0] is plugins._run_react
    assert call.kwargs["id"].startswith("react-")
    assert call.kwargs["args"][1] == "lamp"


def test_switch_off_cancels_pending_jobs(ctx, configured):
    _, listener = configured
    _switch(ctx, listener, 1, m.OFF, m.ON)  # rule 0 has --delay, so it goes pending
    ctx.scheduler.reset_mock()
    _switch(ctx, listener, 1, m.ON, m.OFF)  # reverse edge cancels the pending
    assert ctx.scheduler.remove_job.called


def test_unwatched_device_is_ignored(ctx, configured):
    _, listener = configured
    device = m.DeviceState(id=99, name="other", attributes={"switch": m.ON}, last_activity=None)
    listener.func(*listener.args, device, "switch", m.OFF, m.ON)
    ctx.scheduler.add_job.assert_not_called()
    ctx.api.dispatch.assert_not_called()


def test_run_react_dispatches_the_action(ctx):
    _install(ctx, _make(m.Devices(Light.lamp), "switch", m.ON, m.OFF, delay=10), {1: Light.lamp})
    device = m.DeviceState(id=1, name="lamp", attributes={"switch": m.ON}, last_activity=None)
    plugins._on_event(ctx, ctx.sources, device, "switch", m.OFF, m.ON)
    _fire_pending(ctx)
    assert _dispatched(ctx) == [(Light.lamp, m.OFF)]


def test_consecutive_fires_log_the_same_trigger_id(ctx):
    _install(ctx, _make(m.Devices(Light.lamp), "switch", m.ON, m.OFF), {1: Light.lamp})
    device = m.DeviceState(id=1, name="lamp", attributes={"switch": m.ON}, last_activity=None)
    plugins._on_event(ctx, ctx.sources, device, "switch", m.OFF, m.ON)
    ctx.api.local_now.return_value = _NOW + timedelta(seconds=plugins.COOLDOWN.total_seconds() + 1)
    plugins._on_event(ctx, ctx.sources, device, "switch", m.OFF, m.ON)
    assert [call.kwargs["trigger"] for call in ctx.api.log.call_args_list] == [_hub("1"), _hub("1")]


def test_a_different_device_logs_a_different_trigger_id(ctx):
    _install(
        ctx,
        [
            *_make(m.Devices(Light.lamp), "switch", m.ON, m.OFF),
            *_make(m.Devices(Light.desk), "switch", m.ON, m.OFF),
        ],
        {1: Light.lamp, 2: Light.desk},
    )
    lamp = m.DeviceState(id=1, name="lamp", attributes={"switch": m.ON}, last_activity=None)
    desk = m.DeviceState(id=2, name="desk", attributes={"switch": m.ON}, last_activity=None)
    plugins._on_event(ctx, ctx.sources, lamp, "switch", m.OFF, m.ON)
    plugins._on_event(ctx, ctx.sources, desk, "switch", m.OFF, m.ON)
    assert [call.kwargs["trigger"] for call in ctx.api.log.call_args_list] == [_hub("1"), _hub("2")]


def test_untargeted_ac_command_targets_the_ac_set(ctx, configured):
    rules, _ = configured
    assert rules[4].items[0].command.channel == m.Devices(Ac)


def test_targeted_action_goes_to_the_target(ctx):
    _install(ctx, _make(m.Devices(Light.lamp), "switch", m.ON, m.OFF, target=m.Devices(Light.desk)), {1: Light.lamp})
    device = m.DeviceState(id=1, name="lamp", attributes={"switch": m.ON}, last_activity=None)
    plugins._on_event(ctx, ctx.sources, device, "switch", m.OFF, m.ON)
    assert _dispatched(ctx) == [(Light.desk, m.OFF)]


def test_targeted_rule_schedules_with_the_target(ctx):
    _install(ctx, _make(m.Devices(Light.lamp), "switch", m.ON, m.OFF, target=m.Devices(Light.desk), delay=5), {1: Light.lamp})
    device = m.DeviceState(id=1, name="lamp", attributes={"switch": m.ON}, last_activity=None)
    plugins._on_event(ctx, ctx.sources, device, "switch", m.OFF, m.ON)
    _fire_pending(ctx)
    assert _dispatched(ctx) == [(Light.desk, m.OFF)]


def test_contact_open_triggers_immediate_rule(ctx):
    rule = _make(m.Devices(Light.lamp), "contact", "open", m.AcCommand(m.AcMode.FAN_ONLY, "low", 75), target=m.Devices(Ac))
    _install(ctx, rule, {56: Light.lamp})
    device = m.DeviceState(id=56, name="balcony door", attributes={"contact": "open"}, last_activity=None)
    plugins._on_event(ctx, ctx.sources, device, "contact", "closed", "open")
    assert _dispatched(ctx) == [(Ac.living, m.AcCommand(m.AcMode.FAN_ONLY, "low", 75))]


def test_if_clause_parses_device_and_condition(ctx, configured):
    rules, _ = configured
    assert rules[2].items[0].conditions[0] == plugins.AcIs(m.AcChannel(Ac.living), m.AcState.ON)
    assert rules[3].items[0].conditions[0] == plugins.AcIs(m.AcChannel(Ac.living), m.AcState.COOL)


def test_set_clause_parses_explicit_target(ctx, configured):
    rules, _ = configured
    assert rules[0].items[0].command.channel == m.Devices(Light.lamp)
    assert rules[2].items[0].command.channel == m.Devices(Ac.living)


def test_target_must_match_action_kind():
    objects = {"device": SimpleNamespace(enums={"Light": Light, "AC": Ac})}
    with pytest.raises(ValueError):
        react._parse_target("Light.desk", m.AcCommand(m.AcMode.COOL, "low", 75), objects)
    with pytest.raises(ValueError):
        react._parse_target("AC", m.STOP, objects)


def test_if_clause_covers_lights_and_chromecasts(ctx, configured):
    rules, _ = configured
    assert rules[5].items[0].conditions[0] == engine.Is(m.MqttDeviceChannel(Light.desk, "switch"), m.ON)
    assert rules[6].items[0].conditions[0] == engine.Is(m.CastChannel(Chromecast.tv), m.Playback.PLAYING)


def test_motion_trigger_with_target_and_no_if_clause(ctx, configured):
    # regression: docopt's optional-group matching lets the bracketed `if <device> is
    # <condition>` absorb a stray token even without the literal if/is present, which
    # made a bare `set <target> <action>` (no if clause) misparse as `set <action>`
    rules, _ = configured
    assert rules[7].trigger == engine.Transition(m.MqttDeviceChannel(Sensor.living, "motion"), "active")
    assert rules[7].items[0].conditions == ()
    assert rules[7].items[0].command == engine.Command(m.Devices(Light.lamp), m.ON)


def test_when_requires_a_known_condition():
    objects = {"device": SimpleNamespace(enums={"Light": Light, "AC": Ac, "Chromecast": Chromecast})}
    with pytest.raises(ValueError):
        react._parse_when(Ac.living, "heat", objects)
    with pytest.raises(ValueError):
        react._parse_when(Light.desk, "cool", objects)
    with pytest.raises(ValueError):
        react._parse_when(Chromecast.tv, "on", objects)


def test_when_gates_immediate_rule_on_ac_state(ctx):
    when = plugins.When(Ac.living, m.AcState.ON)
    rule = _make(m.Devices(Light.lamp), "contact", "open", m.AcCommand(m.AcMode.FAN_ONLY, "low", 75), target=m.Devices(Ac), when=when)
    _install(ctx, rule, {56: Light.lamp})
    device = m.DeviceState(id=56, name="balcony door", attributes={"contact": "open"}, last_activity=None)
    ctx.api.capture_acs.return_value = (m.AcStatus(Ac.living, m.AcState.OFF),)
    plugins._on_event(ctx, ctx.sources, device, "contact", "closed", "open")
    ctx.api.dispatch.assert_not_called()
    ctx.api.log.assert_not_called()
    ctx.api.capture_acs.return_value = (m.AcStatus(Ac.living, m.AcState.COOL),)
    plugins._on_event(ctx, ctx.sources, device, "contact", "closed", "open")
    assert _dispatched(ctx) == [(Ac.living, m.AcCommand(m.AcMode.FAN_ONLY, "low", 75))]


def test_when_mode_predicate_requires_that_mode(ctx):
    when = plugins.When(Ac.living, m.AcState.COOL)
    _install(ctx, _make(m.Devices(Light.lamp), "switch", m.ON, m.OFF, delay=10, when=when), {1: Light.lamp})
    device = m.DeviceState(id=1, name="lamp", attributes={"switch": m.ON}, last_activity=None)
    ctx.api.capture_acs.return_value = (m.AcStatus(Ac.living, m.AcState.FAN_ONLY),)
    plugins._on_event(ctx, ctx.sources, device, "switch", m.OFF, m.ON)
    _fire_pending(ctx)
    ctx.api.dispatch.assert_not_called()
    ctx.api.log.assert_not_called()
    ctx.api.capture_acs.return_value = (m.AcStatus(Ac.living, m.AcState.COOL),)
    plugins._on_event(ctx, ctx.sources, device, "switch", m.OFF, m.ON)
    _fire_pending(ctx)
    ctx.api.dispatch.assert_called_once()


def test_when_on_ignores_unknown_mode_for_a_specific_mode_query(ctx):
    when = plugins.When(Ac.living, m.AcState.COOL)
    _install(ctx, _make(m.Devices(Light.lamp), "switch", m.ON, m.OFF, delay=10, when=when), {1: Light.lamp})
    device = m.DeviceState(id=1, name="lamp", attributes={"switch": m.ON}, last_activity=None)
    ctx.api.capture_acs.return_value = (m.AcStatus(Ac.living, m.AcState.ON),)
    plugins._on_event(ctx, ctx.sources, device, "switch", m.OFF, m.ON)
    _fire_pending(ctx)
    ctx.api.dispatch.assert_not_called()


def test_when_checks_chromecast_playback_at_fire_time(ctx):
    when = plugins.When(Chromecast.tv, m.Playback.PLAYING)
    _install(ctx, _make(m.Devices(Light.lamp), "switch", m.ON, m.OFF, delay=10, when=when), {1: Light.lamp})
    device = m.DeviceState(id=1, name="lamp", attributes={"switch": m.ON}, last_activity=None)
    ctx.api.capture_sounds.return_value = (m.SoundState(Chromecast.tv, None, 30, m.Playback.STOPPED),)
    plugins._on_event(ctx, ctx.sources, device, "switch", m.OFF, m.ON)
    _fire_pending(ctx)
    ctx.api.dispatch.assert_not_called()
    ctx.api.capture_sounds.return_value = (m.SoundState(Chromecast.tv, "stream", 30, m.Playback.PLAYING),)
    plugins._on_event(ctx, ctx.sources, device, "switch", m.OFF, m.ON)
    _fire_pending(ctx)
    ctx.api.dispatch.assert_called_once()


def test_when_checks_hubitat_state_at_fire_time(ctx):
    when = plugins.When(Light.desk, m.ON)
    _install(ctx, _make(m.Devices(Light.lamp), "switch", m.ON, m.OFF, delay=10, when=when), {1: Light.lamp})
    device = m.DeviceState(id=1, name="lamp", attributes={"switch": m.ON}, last_activity=None)
    ctx.api.device_states.return_value = [m.DeviceState(id=2, name="desk", attributes={"switch": m.OFF}, last_activity=None)]
    plugins._on_event(ctx, ctx.sources, device, "switch", m.OFF, m.ON)
    _fire_pending(ctx)
    ctx.api.dispatch.assert_not_called()
    ctx.api.device_states.return_value = [m.DeviceState(id=2, name="desk", attributes={"switch": m.ON}, last_activity=None)]
    plugins._on_event(ctx, ctx.sources, device, "switch", m.OFF, m.ON)
    _fire_pending(ctx)
    ctx.api.dispatch.assert_called_once()


def test_reader_resolves_ac_playback_and_attr(ctx):
    ctx.api.capture_acs.return_value = (m.AcStatus(Ac.living, m.AcState.COOL),)
    ctx.api.capture_sounds.return_value = (m.SoundState(Chromecast.tv, "s", 30, m.Playback.PLAYING),)
    ctx.api.device_states.return_value = [m.DeviceState(id=2, name="desk", attributes={"switch": m.ON}, last_activity=None)]
    read = plugins._reader(ctx)
    assert read(m.AcChannel(Ac.living)) == m.AcState.COOL
    assert read(m.CastChannel(Chromecast.tv)) == m.Playback.PLAYING
    assert read(m.MqttDeviceChannel(Light.desk, "switch")) == m.ON


def test_reader_formula_evaluates_and_raises_with_context(ctx):
    ctx.api.device_states.return_value = [
        m.DeviceState(id=5, name="sensor", attributes={"temperature": 77, "humidity": 60}, last_activity=None)
    ]
    read = plugins._reader(ctx)
    assert read(plugins.Formula(Sensor.living, "dewpoint(temperature,humidity)")) == pytest.approx(62.1, abs=0.2)
    ctx.api.device_states.return_value = [m.DeviceState(id=5, name="sensor", attributes={"humidity": 60}, last_activity=None)]
    with pytest.raises(ValueError, match="dewpoint"):
        read(plugins.Formula(Sensor.living, "dewpoint(temperature,humidity)"))


def test_condition_maps_when_by_kind():
    assert plugins.condition(None) == ()
    assert plugins.condition(plugins.When(Ac.living, m.AcState.ON)) == (plugins.AcIs(m.AcChannel(Ac.living), m.AcState.ON),)
    assert plugins.condition(plugins.When(Chromecast.tv, m.Playback.PLAYING)) == (
        engine.Is(m.CastChannel(Chromecast.tv), m.Playback.PLAYING),
    )
    assert plugins.condition(plugins.When(Light.desk, m.ON)) == (engine.Is(m.MqttDeviceChannel(Light.desk, "switch"), m.ON),)


def test_ac_is_bitmask_respects_flag_membership():
    assert not plugins.AcIs(m.AcChannel(Ac.living), m.AcState.COOL).holds(lambda channel: m.AcState.ON)
    assert plugins.AcIs(m.AcChannel(Ac.living), m.AcState.ON).holds(lambda channel: m.AcState.COOL)
    assert not plugins.AcIs(m.AcChannel(Ac.living), m.AcState.COOL).holds(lambda channel: None)


def _make_range(sensor, expr, low, high, target, action, people=None):
    formula = plugins.Formula(sensor, expr)
    conditions = []
    if people:
        conditions.append(plugins.Present(people))
    if isinstance(action, m.AcCommand):
        conditions.extend(plugins.AcIs(m.AcChannel(ac), m.AcState.OFF) for ac in target.all())
    conditions.append(plugins.Range(formula, low, high))
    command = engine.Command(target, action)
    return [engine.Rule(plugins.DeviceChanged(sensor, expr), (engine.Clause(tuple(conditions), command),), cooldown=plugins.COOLDOWN)]


def _range_event(ctx, sensor, attributes):
    device = m.DeviceState(id=sensor.value, name="sensor", attributes=attributes, last_activity=None)
    ctx.api.device_states.return_value = [device]
    changed = next(iter(attributes))
    plugins._on_event(ctx, ctx.sources, device, changed, None, attributes[changed])


def test_range_rule_parses_expressions(ctx):
    ctx.config.plugin_configs = {react.CONFIG: (FIXTURE / "react_range.orc").read_text()}
    rules = react.setup(ctx)
    temp = plugins.Formula(Sensor.living, "temperature")
    dewpoint = plugins.Formula(Sensor.living, "dewpoint(temperature,humidity)")
    assert rules[0].trigger == plugins.DeviceChanged(Sensor.living, "temperature")
    assert rules[0].items[0].conditions == (plugins.AcIs(m.AcChannel(Ac.living), m.AcState.OFF), plugins.Range(temp, 68, 75))
    assert rules[0].items[0].command == engine.Command(m.Devices(Ac), m.AcCommand(m.AcMode.COOL, "low", 72))
    assert rules[1].trigger == plugins.DeviceChanged(Sensor.living, "dewpoint(temperature,humidity)")
    assert rules[1].items[0].conditions == (
        plugins.Present(("alice", "bob")),
        plugins.AcIs(m.AcChannel(Ac.living), m.AcState.OFF),
        plugins.Range(dewpoint, 50, 60),
    )
    assert rules[2].trigger == plugins.DeviceChanged(Sensor.living, "dewpoint(temperature,humidity)")
    assert rules[2].items[0].conditions == (engine.Is(m.AnyoneChannel(), True), plugins.Range(dewpoint, 59, 104))


def test_value_in_range_sets_the_ac(ctx):
    ac = m.AcCommand(m.AcMode.COOL, "low", 72)
    _install(ctx, _make_range(Sensor.living, "temperature", 68, 75, m.Devices(Ac), ac), {5: Sensor.living})
    _range_event(ctx, Sensor.living, {"temperature": 70})
    assert _dispatched(ctx) == [(Ac.living, ac)]


def test_value_in_range_does_nothing_if_ac_already_on(ctx):
    ac = m.AcCommand(m.AcMode.COOL, "low", 72)
    _install(ctx, _make_range(Sensor.living, "temperature", 68, 75, m.Devices(Ac), ac), {5: Sensor.living})
    ctx.api.capture_acs.return_value = (m.AcStatus(Ac.living, m.AcState.COOL),)
    _range_event(ctx, Sensor.living, {"temperature": 70})
    ctx.api.dispatch.assert_not_called()


def _past_cooldown(steps):
    return _NOW + timedelta(seconds=steps * (plugins.COOLDOWN.total_seconds() + 1))


def test_ac_turned_off_manually_does_not_self_correct_until_range_reentered(ctx):
    ac = m.AcCommand(m.AcMode.COOL, "low", 72)
    _install(ctx, _make_range(Sensor.living, "temperature", 68, 75, m.Devices(Ac), ac), {5: Sensor.living})
    _range_event(ctx, Sensor.living, {"temperature": 70})
    assert _dispatched(ctx) == [(Ac.living, ac)]  # AC comes on

    # someone (or something) turns the AC back off while the temperature is still in range
    ctx.api.capture_acs.return_value = (m.AcStatus(Ac.living, m.AcState.OFF),)
    ctx.api.dispatch.reset_mock()
    ctx.api.local_now.return_value = _past_cooldown(1)
    _range_event(ctx, Sensor.living, {"temperature": 71})
    ctx.api.dispatch.assert_not_called()  # doesn't self-correct

    ctx.api.local_now.return_value = _past_cooldown(2)
    _range_event(ctx, Sensor.living, {"temperature": 80})  # genuinely leaves the range
    ctx.api.dispatch.assert_not_called()

    ctx.api.local_now.return_value = _past_cooldown(3)
    _range_event(ctx, Sensor.living, {"temperature": 71})  # and re-enters
    assert _dispatched(ctx) == [(Ac.living, ac)]


def test_value_out_of_range_does_nothing(ctx):
    ac = m.AcCommand(m.AcMode.COOL, "low", 72)
    _install(ctx, _make_range(Sensor.living, "temperature", 68, 75, m.Devices(Ac), ac), {5: Sensor.living})
    _range_event(ctx, Sensor.living, {"temperature": 80})
    ctx.api.dispatch.assert_not_called()


def test_computed_expression_over_two_attributes(ctx):
    ac = m.AcCommand(m.AcMode.FAN_ONLY, "low", 70)
    _install(ctx, _make_range(Sensor.living, "dewpoint(temperature,humidity)", 59, 64, m.Devices(Ac), ac), {5: Sensor.living})
    _range_event(ctx, Sensor.living, {"temperature": 77, "humidity": 60})
    assert _dispatched(ctx) == [(Ac.living, ac)]
    ctx.api.dispatch.reset_mock()
    _range_event(ctx, Sensor.living, {"temperature": 77, "humidity": 20})
    ctx.api.dispatch.assert_not_called()


def test_presence_gates_the_range_rule(ctx):
    ac = m.AcCommand(m.AcMode.COOL, "low", 72)
    _install(ctx, _make_range(Sensor.living, "temperature", 68, 75, m.Devices(Ac), ac, people=("alice", "bob")), {5: Sensor.living})
    ctx.api.present_names.return_value = set()
    _range_event(ctx, Sensor.living, {"temperature": 70})
    ctx.api.dispatch.assert_not_called()
    ctx.api.present_names.return_value = {"alice"}
    _range_event(ctx, Sensor.living, {"temperature": 70})
    assert _dispatched(ctx) == [(Ac.living, ac)]


def test_presence_anyone_gates_the_range_rule(ctx):
    ac = m.AcCommand(m.AcMode.COOL, "low", 72)
    formula = plugins.Formula(Sensor.living, "temperature")
    conditions = (engine.Is(m.AnyoneChannel(), True), plugins.AcIs(m.AcChannel(Ac.living), m.AcState.OFF), plugins.Range(formula, 68, 75))
    command = engine.Command(m.Devices(Ac), ac)
    rule = engine.Rule(
        plugins.DeviceChanged(Sensor.living, "temperature"), (engine.Clause(conditions, command),), cooldown=plugins.COOLDOWN
    )
    _install(ctx, [rule], {5: Sensor.living})
    ctx.api.present_names.return_value = set()
    _range_event(ctx, Sensor.living, {"temperature": 70})
    ctx.api.dispatch.assert_not_called()
    ctx.api.present_names.return_value = {"alice"}
    _range_event(ctx, Sensor.living, {"temperature": 70})
    assert _dispatched(ctx) == [(Ac.living, ac)]
