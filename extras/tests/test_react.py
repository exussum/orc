from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, create_autospec
from zoneinfo import ZoneInfo

import pytest
from apscheduler.schedulers.base import BaseScheduler
from orc_extras import react
from orc_extras.react import plugins

import orc
from orc import api
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


@pytest.fixture(autouse=True)
def _device_enums(monkeypatch):
    from orc import declarations

    monkeypatch.setattr(orc.config, "registry", declarations.Declarations().build({"Light": Light, "AC": Ac, "Chromecast": Chromecast}))


@pytest.fixture
def ctx():
    mock = MagicMock()
    mock.api = create_autospec(api)
    mock.scheduler = create_autospec(BaseScheduler, instance=True)
    mock.api.JOBSTORE_MEMORY = "memory"
    mock.api.local_now.return_value = _NOW
    mock.config.settings.tz = _UTC
    mock.config.registry = orc.config.registry
    return mock


def _setup(ctx):
    ctx.config.plugin_configs = {react.CONFIG: (FIXTURE / "react.orc").read_text()}
    react.setup(ctx)
    listener = ctx.api.add_listener.call_args.args[0]
    _, rules, _ = listener.args
    return rules, listener


def _switch(ctx, listener, device_id, old, new):
    device = m.DeviceState(id=device_id, name="lamp", attributes={"switch": new}, last_activity=None)
    listener.func(*listener.args, device, "switch", old, new)


def _dispatched(ctx):
    return [(c.what.one(), c.state) for c in ctx.api.dispatch.call_args.args[0].items]


def test_config_registers_listener(ctx):
    rules, _ = _setup(ctx)
    index, rule, by_id = rules[0]
    assert index == 0
    assert rule.attribute == "switch"
    assert rule.state == m.ON
    assert rule.action == m.OFF
    assert rule.delay == 10
    assert by_id == {"1": Light.lamp, "2": Light.desk}


def test_switch_on_schedules_reaction(ctx):
    _, listener = _setup(ctx)
    _switch(ctx, listener, 1, m.OFF, m.ON)
    call = ctx.scheduler.add_job.call_args
    assert call.args[0] is plugins._run_react
    assert call.kwargs["id"] == "react-0-1"
    assert call.kwargs["args"] == (m.Devices(Light.lamp), "lamp", m.ON, m.OFF, 10, None)


def test_switch_off_cancels_pending_jobs(ctx):
    _, listener = _setup(ctx)
    _switch(ctx, listener, 1, m.ON, m.OFF)
    assert ctx.scheduler.remove_job.call_args_list == [call(f"react-{index}-1", jobstore="memory") for index in (0, 4, 5)]


def test_cooldown_suppresses_rapid_re_trigger(ctx):
    rule = react.Rule(m.Devices(Light.lamp), "switch", m.ON, m.OFF, None, None, None)
    rules = [(0, rule, {"1": Light.lamp})]
    cooldowns: dict = {}
    device = m.DeviceState(id=1, name="lamp", attributes={"switch": m.ON}, last_activity=None)
    plugins._on_event(ctx, rules, cooldowns, device, "switch", m.OFF, m.ON)
    plugins._on_event(ctx, rules, cooldowns, device, "switch", m.OFF, m.ON)
    ctx.api.dispatch.assert_called_once()  # second is within the cooldown window
    ctx.api.local_now.return_value = _NOW + timedelta(seconds=11)
    plugins._on_event(ctx, rules, cooldowns, device, "switch", m.OFF, m.ON)
    assert ctx.api.dispatch.call_count == 2  # window elapsed, fires again


def test_unwatched_device_is_ignored(ctx):
    _, listener = _setup(ctx)
    device = m.DeviceState(id=99, name="other", attributes={"switch": m.ON}, last_activity=None)
    listener.func(*listener.args, device, "switch", m.OFF, m.ON)
    ctx.scheduler.add_job.assert_not_called()


def test_run_react_dispatches_the_action(ctx):
    plugins._run_react.__wrapped__(m.Devices(Light.lamp), "lamp", m.ON, m.OFF, 10, None, ctx=ctx)
    assert _dispatched(ctx) == [(Light.lamp, m.OFF)]


def test_untargeted_ac_command_targets_the_ac_set(ctx):
    rules, _ = _setup(ctx)
    assert rules[3][1].target == m.Devices(Ac)


def test_targeted_action_goes_to_the_target(ctx):
    rule = react.Rule(m.Devices(Light.lamp), "switch", m.ON, m.OFF, m.Devices(Light.desk), None, None)
    rules = [(0, rule, {"1": Light.lamp})]
    device = m.DeviceState(id=1, name="lamp", attributes={"switch": m.ON}, last_activity=None)
    plugins._on_event(ctx, rules, {}, device, "switch", m.OFF, m.ON)
    assert _dispatched(ctx) == [(Light.desk, m.OFF)]


def test_targeted_rule_schedules_with_the_target(ctx):
    rule = react.Rule(m.Devices(Light.lamp), "switch", m.ON, m.OFF, m.Devices(Light.desk), 5, None)
    rules = [(0, rule, {"1": Light.lamp})]
    device = m.DeviceState(id=1, name="lamp", attributes={"switch": m.ON}, last_activity=None)
    plugins._on_event(ctx, rules, {}, device, "switch", m.OFF, m.ON)
    assert ctx.scheduler.add_job.call_args.kwargs["args"] == (m.Devices(Light.desk), "lamp", m.ON, m.OFF, 5, None)


def test_contact_open_triggers_immediate_rule(ctx):
    rule = react.Rule(m.Devices(Light.lamp), "contact", "open", m.AcCommand(m.AcMode.FAN_ONLY, "low", 75), m.Devices(Ac), None, None)
    rules = [(0, rule, {"56": Light.lamp})]
    device = m.DeviceState(id=56, name="balcony door", attributes={"contact": "open"}, last_activity=None)
    plugins._on_event(ctx, rules, {}, device, "contact", "closed", "open")
    assert _dispatched(ctx) == [(Ac.living, m.AcCommand(m.AcMode.FAN_ONLY, "low", 75))]


def test_if_clause_parses_device_and_condition(ctx):
    rules, _ = _setup(ctx)
    assert rules[1][1].when == plugins.When(Ac.living, m.AcState.ON)
    assert rules[2][1].when == plugins.When(Ac.living, m.AcState.COOL)


def test_set_clause_parses_explicit_target(ctx):
    rules, _ = _setup(ctx)
    assert rules[0][1].target is None
    assert rules[1][1].target == m.Devices(Ac.living)


def test_target_must_match_action_kind():
    objects = {"device": SimpleNamespace(enums={"Light": Light, "AC": Ac})}
    with pytest.raises(ValueError):
        react._parse_target("Light.desk", m.AcCommand(m.AcMode.COOL, "low", 75), objects)
    with pytest.raises(ValueError):
        react._parse_target("AC", m.STOP, objects)


def test_if_clause_covers_lights_and_chromecasts(ctx):
    rules, _ = _setup(ctx)
    assert rules[4][1].when == plugins.When(Light.desk, m.ON)
    assert rules[5][1].when == plugins.When(Chromecast.tv, m.Playback.PLAYING)


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
    rule = react.Rule(m.Devices(Light.lamp), "contact", "open", m.AcCommand(m.AcMode.FAN_ONLY, "low", 75), m.Devices(Ac), None, when)
    rules = [(0, rule, {"56": Light.lamp})]
    device = m.DeviceState(id=56, name="balcony door", attributes={"contact": "open"}, last_activity=None)
    ctx.api.capture_acs.return_value = m.Configs(m.AcStatus(Ac.living, m.AcState.OFF))
    plugins._on_event(ctx, rules, {}, device, "contact", "closed", "open")
    ctx.api.dispatch.assert_not_called()
    ctx.api.log.assert_called_with(plugins.Log.REACT, "`balcony door` open — skipped, `living` is not on")
    ctx.api.capture_acs.return_value = m.Configs(m.AcStatus(Ac.living, m.AcState.COOL))
    plugins._on_event(ctx, rules, {}, device, "contact", "closed", "open")
    assert _dispatched(ctx) == [(Ac.living, m.AcCommand(m.AcMode.FAN_ONLY, "low", 75))]


def test_when_mode_predicate_requires_that_mode(ctx):
    when = plugins.When(Ac.living, m.AcState.COOL)
    ctx.api.capture_acs.return_value = m.Configs(m.AcStatus(Ac.living, m.AcState.FAN_ONLY))
    plugins._run_react.__wrapped__(m.Devices(Light.lamp), "lamp", m.ON, m.OFF, 10, when, ctx=ctx)
    ctx.api.dispatch.assert_not_called()
    ctx.api.log.assert_called_with(plugins.Log.REACT, "`lamp` on 10m ago — skipped, `living` is not cool")
    ctx.api.capture_acs.return_value = m.Configs(m.AcStatus(Ac.living, m.AcState.COOL))
    plugins._run_react.__wrapped__(m.Devices(Light.lamp), "lamp", m.ON, m.OFF, 10, when, ctx=ctx)
    ctx.api.dispatch.assert_called_once()


def test_when_on_ignores_unknown_mode_for_a_specific_mode_query(ctx):
    # an AC powered but with unknown mode (bare ON) must not satisfy `is cool`
    when = plugins.When(Ac.living, m.AcState.COOL)
    ctx.api.capture_acs.return_value = m.Configs(m.AcStatus(Ac.living, m.AcState.ON))
    plugins._run_react.__wrapped__(m.Devices(Light.lamp), "lamp", m.ON, m.OFF, 10, when, ctx=ctx)
    ctx.api.dispatch.assert_not_called()


def test_when_checks_chromecast_playback_at_fire_time(ctx):
    when = plugins.When(Chromecast.tv, m.Playback.PLAYING)
    ctx.api.capture_sounds.return_value = m.Configs(m.SoundState(Chromecast.tv, None, 30, m.Playback.STOPPED))
    plugins._run_react.__wrapped__(m.Devices(Light.lamp), "lamp", m.ON, m.OFF, 10, when, ctx=ctx)
    ctx.api.dispatch.assert_not_called()
    ctx.api.capture_sounds.return_value = m.Configs(m.SoundState(Chromecast.tv, "stream", 30, m.Playback.PLAYING))
    plugins._run_react.__wrapped__(m.Devices(Light.lamp), "lamp", m.ON, m.OFF, 10, when, ctx=ctx)
    ctx.api.dispatch.assert_called_once()


def test_when_checks_hubitat_state_at_fire_time(ctx):
    when = plugins.When(Light.desk, m.ON)
    ctx.api.device_states.return_value = [m.DeviceState(id=2, name="desk", attributes={"switch": m.OFF}, last_activity=None)]
    plugins._run_react.__wrapped__(m.Devices(Light.lamp), "lamp", m.ON, m.OFF, 10, when, ctx=ctx)
    ctx.api.dispatch.assert_not_called()
    ctx.api.device_states.return_value = [m.DeviceState(id=2, name="desk", attributes={"switch": m.ON}, last_activity=None)]
    plugins._run_react.__wrapped__(m.Devices(Light.lamp), "lamp", m.ON, m.OFF, 10, when, ctx=ctx)
    ctx.api.dispatch.assert_called_once()
