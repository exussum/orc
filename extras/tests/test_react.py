from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, create_autospec
from zoneinfo import ZoneInfo

import pytest
from apscheduler.schedulers.base import BaseScheduler
from orc_extras import react
from orc_extras.react import plugins

import orc
from orc import api
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


@pytest.fixture(autouse=True)
def _device_enums(monkeypatch):
    from orc.kernel import declarations

    monkeypatch.setattr(orc.config, "registry", declarations.Declarations().build({"Light": Light, "AC": Ac, "Chromecast": Chromecast}))


@pytest.fixture
def ctx():
    mock = MagicMock()
    mock.api = create_autospec(api)
    mock.engine = engine.Runtime([])
    mock.scheduler = create_autospec(BaseScheduler, instance=True)
    mock.api.JOBSTORE_MEMORY = "memory"
    mock.api.local_now.return_value = _NOW
    mock.config.settings.tz = _UTC
    mock.config.registry = orc.config.registry
    mock.plugin_state = {}
    return mock


def _setup(ctx):
    ctx.config.plugin_configs = {react.CONFIG: (FIXTURE / "react.orc").read_text()}
    react.setup(ctx)
    rules = list(ctx.plugin_state[plugins].rules.values())
    listener = ctx.api.add_listener.call_args.args[0]
    return rules, listener


def _make(devices, attribute, state, action, target=None, delay=None, when=None):
    cond = plugins.condition(when)
    span = timedelta(minutes=delay) if delay else timedelta()
    rules = []
    for source in devices.all():
        command = engine.Command(target or m.Devices(source), action)
        trigger = engine.Transition(m.MqttDeviceChannel(source, attribute), state)
        rules.append(engine.Rule(trigger, (engine.Clause(cond, command),), span, cooldown=plugins.COOLDOWN))
    return rules


def _install(ctx, engine_rules, sources):
    ctx.engine.add_rules(engine_rules)
    ctx.plugin_state[plugins] = plugins.React({hash(er): er for er in engine_rules}, sources)


def _switch(ctx, listener, device_id, old, new):
    device = m.DeviceState(id=device_id, name="lamp", attributes={"switch": new}, last_activity=None)
    listener.func(*listener.args, device, "switch", old, new)


def _dispatched(ctx):
    return [(c.channel.one(), c.value) for c in ctx.api.dispatch.call_args.args[0]]


def _fire_pending(ctx):
    deferred, name = ctx.scheduler.add_job.call_args.kwargs["args"]
    plugins._run_react.__wrapped__(deferred, name, ctx=ctx)


# The fixture's first line (`react Light ...`) fans out to lamp + desk, so the
# compiled rules are: 0 lamp/on, 1 desk/on, 2..6 the single-device lines 2..6.
def test_config_registers_listener(ctx):
    rules, _ = _setup(ctx)
    assert len(rules) == 7  # line 1 fans out to lamp + desk; lines 2..6 are single-device
    assert rules[0].trigger == engine.Transition(m.MqttDeviceChannel(Light.lamp, "switch"), m.ON)
    assert rules[1].trigger == engine.Transition(m.MqttDeviceChannel(Light.desk, "switch"), m.ON)
    assert rules[0].items[0].command == engine.Command(m.Devices(Light.lamp), m.OFF)
    assert rules[0].delay == timedelta(minutes=10)
    assert ctx.plugin_state[plugins].sources == {1: Light.lamp, 2: Light.desk}


def test_switch_on_schedules_reaction(ctx):
    _setup(ctx)
    listener = ctx.api.add_listener.call_args.args[0]
    _switch(ctx, listener, 1, m.OFF, m.ON)
    call = ctx.scheduler.add_job.call_args
    assert call.args[0] is plugins._run_react
    assert call.kwargs["id"].startswith("react-")
    assert call.kwargs["args"][1] == "lamp"


def test_switch_off_cancels_pending_jobs(ctx):
    _, listener = _setup(ctx)
    _switch(ctx, listener, 1, m.OFF, m.ON)  # rule 0 has --delay, so it goes pending
    ctx.scheduler.reset_mock()
    _switch(ctx, listener, 1, m.ON, m.OFF)  # reverse edge cancels the pending
    assert ctx.scheduler.remove_job.called


def test_unwatched_device_is_ignored(ctx):
    _, listener = _setup(ctx)
    device = m.DeviceState(id=99, name="other", attributes={"switch": m.ON}, last_activity=None)
    listener.func(*listener.args, device, "switch", m.OFF, m.ON)
    ctx.scheduler.add_job.assert_not_called()
    ctx.api.dispatch.assert_not_called()


def test_run_react_dispatches_the_action(ctx):
    _install(ctx, _make(m.Devices(Light.lamp), "switch", m.ON, m.OFF, delay=10), {1: Light.lamp})
    device = m.DeviceState(id=1, name="lamp", attributes={"switch": m.ON}, last_activity=None)
    plugins._on_event(ctx, device, "switch", m.OFF, m.ON)
    _fire_pending(ctx)
    assert _dispatched(ctx) == [(Light.lamp, m.OFF)]


def test_untargeted_ac_command_targets_the_ac_set(ctx):
    rules, _ = _setup(ctx)
    assert rules[4].items[0].command.channel == m.Devices(Ac)


def test_targeted_action_goes_to_the_target(ctx):
    _install(ctx, _make(m.Devices(Light.lamp), "switch", m.ON, m.OFF, target=m.Devices(Light.desk)), {1: Light.lamp})
    device = m.DeviceState(id=1, name="lamp", attributes={"switch": m.ON}, last_activity=None)
    plugins._on_event(ctx, device, "switch", m.OFF, m.ON)
    assert _dispatched(ctx) == [(Light.desk, m.OFF)]


def test_targeted_rule_schedules_with_the_target(ctx):
    _install(ctx, _make(m.Devices(Light.lamp), "switch", m.ON, m.OFF, target=m.Devices(Light.desk), delay=5), {1: Light.lamp})
    device = m.DeviceState(id=1, name="lamp", attributes={"switch": m.ON}, last_activity=None)
    plugins._on_event(ctx, device, "switch", m.OFF, m.ON)
    _fire_pending(ctx)
    assert _dispatched(ctx) == [(Light.desk, m.OFF)]


def test_contact_open_triggers_immediate_rule(ctx):
    rule = _make(m.Devices(Light.lamp), "contact", "open", m.AcCommand(m.AcMode.FAN_ONLY, "low", 75), target=m.Devices(Ac))
    _install(ctx, rule, {56: Light.lamp})
    device = m.DeviceState(id=56, name="balcony door", attributes={"contact": "open"}, last_activity=None)
    plugins._on_event(ctx, device, "contact", "closed", "open")
    assert _dispatched(ctx) == [(Ac.living, m.AcCommand(m.AcMode.FAN_ONLY, "low", 75))]


def test_if_clause_parses_device_and_condition(ctx):
    rules, _ = _setup(ctx)
    assert rules[2].items[0].conditions[0] == plugins.AcIs(m.AcChannel(Ac.living), m.AcState.ON)
    assert rules[3].items[0].conditions[0] == plugins.AcIs(m.AcChannel(Ac.living), m.AcState.COOL)


def test_set_clause_parses_explicit_target(ctx):
    rules, _ = _setup(ctx)
    assert rules[0].items[0].command.channel == m.Devices(Light.lamp)
    assert rules[2].items[0].command.channel == m.Devices(Ac.living)


def test_target_must_match_action_kind():
    objects = {"device": SimpleNamespace(enums={"Light": Light, "AC": Ac})}
    with pytest.raises(ValueError):
        react._parse_target("Light.desk", m.AcCommand(m.AcMode.COOL, "low", 75), objects)
    with pytest.raises(ValueError):
        react._parse_target("AC", m.STOP, objects)


def test_if_clause_covers_lights_and_chromecasts(ctx):
    rules, _ = _setup(ctx)
    assert rules[5].items[0].conditions[0] == engine.Is(m.MqttDeviceChannel(Light.desk, "switch"), m.ON)
    assert rules[6].items[0].conditions[0] == engine.Is(m.CastChannel(Chromecast.tv), m.Playback.PLAYING)


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
    plugins._on_event(ctx, device, "contact", "closed", "open")
    ctx.api.dispatch.assert_not_called()
    ctx.api.log.assert_called_with(plugins.Log.REACT, "`balcony door` open — skipped, `living` is not on")
    ctx.api.capture_acs.return_value = (m.AcStatus(Ac.living, m.AcState.COOL),)
    plugins._on_event(ctx, device, "contact", "closed", "open")
    assert _dispatched(ctx) == [(Ac.living, m.AcCommand(m.AcMode.FAN_ONLY, "low", 75))]


def test_when_mode_predicate_requires_that_mode(ctx):
    when = plugins.When(Ac.living, m.AcState.COOL)
    _install(ctx, _make(m.Devices(Light.lamp), "switch", m.ON, m.OFF, delay=10, when=when), {1: Light.lamp})
    device = m.DeviceState(id=1, name="lamp", attributes={"switch": m.ON}, last_activity=None)
    ctx.api.capture_acs.return_value = (m.AcStatus(Ac.living, m.AcState.FAN_ONLY),)
    plugins._on_event(ctx, device, "switch", m.OFF, m.ON)
    _fire_pending(ctx)
    ctx.api.dispatch.assert_not_called()
    ctx.api.log.assert_called_with(plugins.Log.REACT, "`lamp` on 10m ago — skipped, `living` is not cool")
    ctx.api.capture_acs.return_value = (m.AcStatus(Ac.living, m.AcState.COOL),)
    plugins._on_event(ctx, device, "switch", m.OFF, m.ON)
    _fire_pending(ctx)
    ctx.api.dispatch.assert_called_once()


def test_when_on_ignores_unknown_mode_for_a_specific_mode_query(ctx):
    when = plugins.When(Ac.living, m.AcState.COOL)
    _install(ctx, _make(m.Devices(Light.lamp), "switch", m.ON, m.OFF, delay=10, when=when), {1: Light.lamp})
    device = m.DeviceState(id=1, name="lamp", attributes={"switch": m.ON}, last_activity=None)
    ctx.api.capture_acs.return_value = (m.AcStatus(Ac.living, m.AcState.ON),)
    plugins._on_event(ctx, device, "switch", m.OFF, m.ON)
    _fire_pending(ctx)
    ctx.api.dispatch.assert_not_called()


def test_when_checks_chromecast_playback_at_fire_time(ctx):
    when = plugins.When(Chromecast.tv, m.Playback.PLAYING)
    _install(ctx, _make(m.Devices(Light.lamp), "switch", m.ON, m.OFF, delay=10, when=when), {1: Light.lamp})
    device = m.DeviceState(id=1, name="lamp", attributes={"switch": m.ON}, last_activity=None)
    ctx.api.capture_sounds.return_value = (m.SoundState(Chromecast.tv, None, 30, m.Playback.STOPPED),)
    plugins._on_event(ctx, device, "switch", m.OFF, m.ON)
    _fire_pending(ctx)
    ctx.api.dispatch.assert_not_called()
    ctx.api.capture_sounds.return_value = (m.SoundState(Chromecast.tv, "stream", 30, m.Playback.PLAYING),)
    plugins._on_event(ctx, device, "switch", m.OFF, m.ON)
    _fire_pending(ctx)
    ctx.api.dispatch.assert_called_once()


def test_when_checks_hubitat_state_at_fire_time(ctx):
    when = plugins.When(Light.desk, m.ON)
    _install(ctx, _make(m.Devices(Light.lamp), "switch", m.ON, m.OFF, delay=10, when=when), {1: Light.lamp})
    device = m.DeviceState(id=1, name="lamp", attributes={"switch": m.ON}, last_activity=None)
    ctx.api.device_states.return_value = [m.DeviceState(id=2, name="desk", attributes={"switch": m.OFF}, last_activity=None)]
    plugins._on_event(ctx, device, "switch", m.OFF, m.ON)
    _fire_pending(ctx)
    ctx.api.dispatch.assert_not_called()
    ctx.api.device_states.return_value = [m.DeviceState(id=2, name="desk", attributes={"switch": m.ON}, last_activity=None)]
    plugins._on_event(ctx, device, "switch", m.OFF, m.ON)
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
