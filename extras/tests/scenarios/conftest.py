import os
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from freezegun import freeze_time
from orc_extras import lg_ac
from orc_extras.lg_ac.model import ACState

import orc
from orc import api, config
from orc import model as m
from orc.dal import net, sqlite
from orc.dal import scheduler as dal_scheduler

MONDAY_AFTERNOON = datetime(2026, 1, 5, 15, tzinfo=config.settings.tz)
AC_ID = "clip-1"
HUB = {
    target: (hub_id, frozenset())
    for hub_id, target in enumerate(
        ("bedroom lamp", "living room desk", "kitchen", "hall", "porch", "office", "scene", "front door motion sensor", "balcony door"), 1
    )
}


class FakeScheduler:
    def __init__(self):
        self.jobs = {}

    def add_job(self, func, trigger, *, args=(), id=None, name=None, **kwargs):
        job = SimpleNamespace(func=func, args=args, id=id or name, name=name, trigger=trigger)
        self.jobs[job.id] = job
        return job

    def get_job(self, id, jobstore=None):
        return self.jobs.get(id)

    def remove_job(self, id, jobstore=None):
        del self.jobs[id]

    def run_due(self, now, ctx):
        for job in [j for j in self.jobs.values() if j.trigger.run_date <= now]:
            del self.jobs[job.id]
            job.func(*job.args, ctx=ctx)


class House:
    def __init__(self, ctx, frozen, listeners, dispatched):
        self.ctx = ctx
        self.frozen = frozen
        self.listeners = listeners
        self.dispatched = dispatched
        self.reported = {}

    def tick(self, **delta):
        self.frozen.tick(timedelta(**delta))
        self.ctx.scheduler.run_due(api.local_now(), self.ctx)

    def motion(self, event):
        self.report(orc.Sensor.ENTRANCE_SENSOR, "motion", event)

    def report(self, device, attribute, new):
        old = self.reported.get((device, attribute))
        state = m.DeviceState(id=device.value, name=device.label, attributes={attribute: new}, last_activity=None)
        for listener in self.listeners:
            listener(state, attribute, old, new)
        self.reported[(device, attribute)] = new
        self.ctx.scheduler.run_due(api.local_now(), self.ctx)

    def ac_reports(self, power, mode=None, fan=None, temperature=None):
        state = ACState(power=power, mode=mode, fan_mode=fan, temperature=temperature)
        report = " ".join(str(part) for part in (power, mode, fan, temperature) if part is not None)
        lg_ac._on_event(self.ctx, AC_ID, f"AC {AC_ID}: {report}", state)

    @staticmethod
    def broker(device):
        return m.Broker(id=str(device.value), source="hubitat")

    @staticmethod
    def log():
        return [(entry.trigger, entry.action, [child.action for child in entry.children]) for entry in reversed(api.log_entries())]


def _load(config_dir, hub):
    os.environ["ORC_CONFIG_DIR"] = config_dir
    config.load(m.Secrets(), hub)


@pytest.fixture
def house(request, monkeypatch, tmp_path):
    sample = os.environ.get("ORC_CONFIG_DIR", "src")
    _load(str(Path(__file__).parent), HUB)
    try:
        monkeypatch.setattr(config, "settings", config.settings._replace(jobs_db=f"sqlite:///{tmp_path / 'state.sqlite'}"))
        sqlite.init_db()
        api._ACTIVITY_LOG.clear()
        net.presence.__init__()
        api.start_ble_listener()
        api.set_ac_handler(lambda *args: None)

        scheduler = FakeScheduler()
        dal_scheduler.set_scheduler(scheduler)
        ctx = m.AppContext(scheduler=scheduler, version_manager=MagicMock())
        api.set_ctx(ctx)

        listeners, dispatched = [], []
        dispatch = api.dispatch

        def record(commands, *args, **kwargs):
            dispatched.append(commands)
            return dispatch(commands, *args, **kwargs)

        with (
            patch.object(api, "add_listener", side_effect=listeners.append),
            patch.object(api, "dispatch", side_effect=record),
            patch.object(net, "scan_presence", return_value=({"Alice"}, [])),
            freeze_time(MONDAY_AFTERNOON) as frozen,
        ):
            for plugin in request.node.get_closest_marker("plugins").args:
                plugin.setup(ctx)
            yield House(ctx, frozen, listeners, dispatched)
    finally:
        _load(sample, {})
