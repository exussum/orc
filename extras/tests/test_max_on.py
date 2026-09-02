from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, create_autospec
from zoneinfo import ZoneInfo

import pytest
from apscheduler.schedulers.base import BaseScheduler
from orc_extras import max_on
from orc_extras.max_on import plugins

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


@pytest.fixture(autouse=True)
def _device_enums(monkeypatch):
    from orc import declarations

    monkeypatch.setattr(orc.config, "registry", declarations.Declarations().build({"Light": Light}))


@pytest.fixture
def ctx():
    mock = MagicMock()
    mock.api = create_autospec(api)
    mock.scheduler = create_autospec(BaseScheduler, instance=True)
    mock.api.JOBSTORE_MEMORY = "memory"
    mock.api.local_now.return_value = _NOW
    mock.config.settings.tz = _UTC
    return mock


def _setup(ctx):
    ctx.config.registry = orc.config.registry
    ctx.config.plugin_configs = {max_on.CONFIG: (FIXTURE / "max_on.orc").read_text()}
    max_on.setup(ctx)
    listener = ctx.api.add_listener.call_args.args[0]
    _, minutes, by_id = listener.args
    return minutes, by_id, listener


def _switch(ctx, listener, device_id, old, new):
    device = m.DeviceState(id=device_id, name="lamp", attributes={"switch": new}, last_activity=None)
    listener.func(*listener.args, device, "switch", old, new)


def test_config_registers_listener(ctx):
    minutes, by_id, _ = _setup(ctx)
    assert minutes == 10
    assert by_id == {1: Light.lamp, 2: Light.desk}


def test_switch_on_schedules_turn_off(ctx):
    _, _, listener = _setup(ctx)
    _switch(ctx, listener, 1, m.OFF, m.ON)
    call = ctx.scheduler.add_job.call_args
    assert call.args[0] is plugins._run_max_on
    assert call.kwargs["id"] == "max-on-1"
    assert call.kwargs["args"] == (Light.lamp, "lamp", 10)


def test_switch_off_cancels_pending_job(ctx):
    _, _, listener = _setup(ctx)
    _switch(ctx, listener, 1, m.ON, m.OFF)
    ctx.scheduler.remove_job.assert_called_once_with("max-on-1", jobstore="memory")


def test_unwatched_device_is_ignored(ctx):
    _, _, listener = _setup(ctx)
    device = m.DeviceState(id=99, name="other", attributes={"switch": m.ON}, last_activity=None)
    listener.func(*listener.args, device, "switch", m.OFF, m.ON)
    ctx.scheduler.add_job.assert_not_called()


def test_run_max_on_turns_the_device_off(ctx):
    plugins._run_max_on.__wrapped__(Light.lamp, "lamp", 10, ctx=ctx)
    dispatched = ctx.api.dispatch.call_args.args[0]
    assert [(c.what.one(), c.state) for c in dispatched.items] == [(Light.lamp, m.OFF)]
