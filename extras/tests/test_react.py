from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, create_autospec
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


@pytest.fixture(autouse=True)
def _device_enums(monkeypatch):
    from orc import declarations

    monkeypatch.setattr(orc.config, "registry", declarations.Declarations().build({"Light": Light, "AC": Ac}))


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
    _, rules = listener.args
    return rules, listener


def _switch(ctx, listener, device_id, old, new):
    device = m.DeviceState(id=device_id, name="lamp", attributes={"switch": new}, last_activity=None)
    listener.func(*listener.args, device, "switch", old, new)


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
    assert call.kwargs["args"] == (Light.lamp, "lamp", m.OFF, 10)


def test_switch_off_cancels_pending_job(ctx):
    _, listener = _setup(ctx)
    _switch(ctx, listener, 1, m.ON, m.OFF)
    ctx.scheduler.remove_job.assert_called_once_with("react-0-1", jobstore="memory")


def test_unwatched_device_is_ignored(ctx):
    _, listener = _setup(ctx)
    device = m.DeviceState(id=99, name="other", attributes={"switch": m.ON}, last_activity=None)
    listener.func(*listener.args, device, "switch", m.OFF, m.ON)
    ctx.scheduler.add_job.assert_not_called()


def test_run_react_dispatches_the_action(ctx):
    plugins._run_react.__wrapped__(Light.lamp, "lamp", m.OFF, 10, ctx=ctx)
    dispatched = ctx.api.dispatch.call_args.args[0]
    assert [(c.what.one(), c.state) for c in dispatched.items] == [(Light.lamp, m.OFF)]


def test_ac_command_targets_every_ac_device(ctx):
    plugins._run_react.__wrapped__(Light.lamp, "door", m.AcCommand(m.AcMode.COOL, "low", 75), 10, ctx=ctx)
    dispatched = ctx.api.dispatch.call_args.args[0]
    assert [(c.what.one(), c.state) for c in dispatched.items] == [(Ac.living, m.AcCommand(m.AcMode.COOL, "low", 75))]


def test_contact_open_triggers_immediate_rule(ctx):
    rule = react.Rule(m.Devices(Light.lamp), "contact", "open", m.AcCommand(m.AcMode.FAN_ONLY, "low", 75), None)
    rules = [(0, rule, {"56": Light.lamp})]
    device = m.DeviceState(id=56, name="balcony door", attributes={"contact": "open"}, last_activity=None)
    plugins._on_event(ctx, rules, device, "contact", "closed", "open")
    dispatched = ctx.api.dispatch.call_args.args[0]
    assert [(c.what.one(), c.state) for c in dispatched.items] == [(Ac.living, m.AcCommand(m.AcMode.FAN_ONLY, "low", 75))]
