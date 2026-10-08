import os
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask
from freezegun import freeze_time
from orc_extras.lg_ac import plugins as lg_ac_plugins

import orc
from orc import api, config, security
from orc import model as m
from orc.dal import net, sqlite
from orc.dal.mqtt import hubitat
from orc.dal.mqtt import paho as mqtt
from orc.plugins import battery, buttons, external
from orc.view import bp

MONDAY_AFTERNOON = datetime(2026, 1, 5, 15, tzinfo=config.settings.tz)
HUB = {
    target: (str(hub_id), frozenset())
    for hub_id, target in enumerate(
        ("bedroom lamp", "living room desk", "kitchen", "hall", "porch", "office", "scene", "front door motion sensor", "balcony door"), 1
    )
}


class FakeScheduler:
    def __init__(self):
        self.jobs = {}

    def once(self, func, when, *args, id=None, name=None, persist=False):
        job = SimpleNamespace(func=func, args=args, id=id or name, name=name, run_date=when)
        self.jobs[job.id] = job
        return job

    def now(self, func, *args, name=None, skip_if_late=False):
        return self.once(func, api.local_now(), *args, name=name)

    def cancel(self, id):
        return self.jobs.pop(id, None) is not None

    def run_due(self, now, ctx):
        while due := [j for j in self.jobs.values() if j.run_date <= now]:
            for job in due:
                del self.jobs[job.id]
                job.func(*job.args, ctx=ctx)


class FakeBleakClient:
    probed: list[str] = []
    reachable: set[str] = set()

    def __init__(self, address, timeout):
        self.address = address
        self.probed.append(address)

    async def __aenter__(self):
        if self.address not in self.reachable:
            raise TimeoutError(self.address)
        return self

    async def __aexit__(self, *exc):
        pass


class House:
    def __init__(self, ctx, client, frozen, listeners, dispatched, lan, probed, pushed, acs):
        self.ctx = ctx
        self.client = client
        self.frozen = frozen
        self.listeners = listeners
        self.dispatched = dispatched
        self.lan = lan
        self.probed = probed
        self.pushed = pushed
        self.reported = {}
        self.acs = acs

    def tick(self, **delta):
        self.frozen.tick(timedelta(**delta))
        self.ctx.scheduler.run_due(api.local_now(), self.ctx)

    def entrance_sensor(self, event):
        self.report(orc.Sensor.ENTRANCE_SENSOR, "motion", event)

    def ac(self, power, **fields):
        unit = orc.AC.LIVING
        old = self.acs.get(str(unit.value), m.AcState())
        self.acs[str(unit.value)] = new = replace(old, power=power, **fields)
        lg_ac_plugins._on_change(self.ctx, m.Status(m.Device(str(unit.value), unit.label, "lg_ac"), "state", old, new, m.Source.ORC))

    def advertise(self, name):
        now = api.local_now()
        key = config.ble_tags.setdefault(name, m.BleKey(name.encode().ljust(32, b"\0"), int(now.timestamp()) - 5000))
        frame = bytes([0x40]) + security.fmdn_eids(key.eik, int(now.timestamp()) - key.anchor)[1]
        net.presence._tags = config.ble_tags
        net.presence._index = net._eid_index(config.ble_tags, net.presence._clocks.copy(), now)
        net.presence._seen(SimpleNamespace(address=f"{name}-tag"), SimpleNamespace(service_data={net.FMDN_SERVICE_UUID: frame}))
        FakeBleakClient.reachable.add(f"{name}-tag")

    def leave(self, name):
        FakeBleakClient.reachable.discard(f"{name}-tag")

    def scan(self):
        api.check_presence(m.Query("lan"))

    def report(self, device, attribute, new):
        old = self.reported.get((device, attribute))
        state = m.DeviceState(m.Device(device.value, device.label, "hubitat"), {attribute: new}, None)
        for listener in self.listeners:
            listener(m.Status(state.device, attribute, old, new, hubitat.HubitatSource.HUBITAT))
        self.reported[(device, attribute)] = new
        self.ctx.scheduler.run_due(api.local_now(), self.ctx)

    def press(self, path):
        assert self.client.get(path).status_code == 200
        self.ctx.scheduler.run_due(api.local_now(), self.ctx)

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
        for name in ("_adapters", "_listeners", "_external_listeners"):
            getattr(mqtt, name).clear()
        net.presence.__init__()
        net.presence._tz = config.settings.tz
        api.start_ble_listener()

        scheduler = FakeScheduler()
        ctx = m.AppContext(scheduler=scheduler, engine=api.runtime())
        api.set_ctx(ctx)

        app = Flask(__name__)
        app.register_blueprint(bp)
        app.orc = ctx

        listeners, dispatched, lan, pushed = [], [], {"Alice"}, []
        FakeBleakClient.probed = []
        FakeBleakClient.reachable = set()
        api.subscribe_push(m.PushSubscription("https://push.example/house", "public-key", "auth-secret"))
        push = SimpleNamespace(public_key=lambda: "", send=lambda subscription, title, body, tag: pushed.append(body))
        dispatch = api.dispatch

        def record(commands, *args, **kwargs):
            dispatched.append(commands)
            return dispatch(commands, *args, **kwargs)

        acs: dict[str, m.AcState] = {}
        mqtt.register(
            SimpleNamespace(
                namespaces=(),
                device_types=("AC",),
                attach=lambda publish: None,
                snapshot=lambda: [m.DeviceState(m.Device(id, "AC", "lg_ac"), asdict(s), None) for id, s in acs.items()],
            )
        )
        with (
            patch.object(mqtt, "add_listener", side_effect=listeners.append),
            patch.object(mqtt, "command"),
            patch.object(api, "dispatch", side_effect=record),
            patch.object(net, "scan_presence", side_effect=lambda pairs: (set(lan), [])),
            patch.object(net, "BleakClient", FakeBleakClient),
            patch.object(config, "providers", config.providers._replace(push=push)),
            freeze_time(MONDAY_AFTERNOON) as frozen,
        ):
            for plugin in request.node.get_closest_marker("plugins").args:
                plugin.setup(ctx)
            for listener in (buttons, battery, external):
                listener.setup(ctx)
            yield House(ctx, app.test_client(), frozen, listeners, dispatched, lan, FakeBleakClient.probed, pushed, acs)
    finally:
        _load(sample, {})
