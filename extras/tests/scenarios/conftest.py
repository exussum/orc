import os
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask
from freezegun import freeze_time

import orc
from orc import api, config, security
from orc import model as m
from orc.dal import broker, net, sqlite
from orc.dal.mqtt import hubitat
from orc.plugins import battery, buttons
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

    def run_due(self, now, ctx, clock=None):
        while due := sorted((j for j in self.jobs.values() if j.run_date <= now), key=lambda j: j.run_date):
            job = due[0]
            del self.jobs[job.id]
            if clock:
                clock.move_to(max(job.run_date, api.local_now()))
            job.func(*job.args, ctx=ctx)
        if clock:
            clock.move_to(now)


class House:
    def __init__(self, ctx, client, frozen, listeners, dispatched, lan, probed, reachable, pushed):
        self.ctx = ctx
        self.client = client
        self.frozen = frozen
        self.listeners = listeners
        self.dispatched = dispatched
        self.lan = lan
        self.probed = probed
        self.reachable = reachable
        self.pushed = pushed
        self.reported = {(light, "switch"): m.OFF for light in orc.Light} | {(sensor, "motion"): "inactive" for sensor in orc.Sensor}

    def tick(self, **delta):
        self.ctx.scheduler.run_due(api.local_now() + timedelta(**delta), self.ctx, clock=self.frozen)

    def entrance_sensor(self, event):
        self.report(orc.Sensor.ENTRANCE_SENSOR, "motion", event)

    def walk_in(self, **after):
        self.entrance_sensor("active")
        self.tick(seconds=30)
        self.entrance_sensor("inactive")
        self.tick(**(after or {"minutes": 2}))

    def drop(self, device, attribute, new):
        self.reported[(device, attribute)] = new

    def come_home(self, name):
        self.lan.clear()
        self.advertise(name)
        self.tick(hours=1)

    def advertise(self, name):
        now = api.local_now()
        if name not in net.presence._tags:
            net.presence.tagged |= {name}
            net.presence.set_key(name, m.BleKey(name.encode().ljust(32, b"\0"), int(now.timestamp()) - 5000))
        key = net.presence._tags[name]
        frame = bytes([0x40]) + security.fmdn_eids(key.eik, int(now.timestamp()) - key.anchor)[1]
        net.presence._index = net._eid_index(net.presence._tags, net.presence._clocks.copy(), now)
        net.presence._seen(SimpleNamespace(address=f"{name}-tag"), SimpleNamespace(service_data={net.FMDN_SERVICE_UUID: frame}))
        self.reachable.add(name)

    def leave(self, name):
        self.reachable.discard(name)

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


@pytest.fixture
def house(request, monkeypatch, tmp_path):
    sample = os.environ.get("ORC_CONFIG_DIR", "src")
    _load(str(Path(__file__).parent), HUB)
    try:
        monkeypatch.setattr(config, "settings", config.settings._replace(jobs_db=f"sqlite:///{tmp_path / 'state.sqlite'}"))
        sqlite.init_db()
        api._ACTIVITY_LOG.clear()
        broker.adapters.clear()
        broker.status_listeners.clear()
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
        api.subscribe_push(m.PushSubscription("https://push.example/house", "public-key", "auth-secret"))
        push = SimpleNamespace(public_key=lambda: "", send=lambda subscription, title, body, tag: pushed.append(body))
        dispatch = api.dispatch
        probed, reachable = [], set()
        real_probe = net.Presence.probe

        def probe(presence, names, trigger):
            probed.extend(sorted(names))
            for name in sorted(names & reachable):
                house.advertise(name)
            real_probe(presence, names, trigger)

        def record(commands, *args, **kwargs):
            dispatched.append(commands)
            return dispatch(commands, *args, **kwargs)

        config.providers.broker.register(
            SimpleNamespace(
                namespaces=(),
                device_types=("AC",),
                attach=lambda publish: None,
                snapshot=lambda: [],
            )
        )
        with (
            patch.object(broker, "add_status_listener", side_effect=listeners.append),
            patch.object(config.providers.broker, "command"),
            patch.object(api, "dispatch", side_effect=record),
            patch.object(net, "scan_presence", side_effect=lambda pairs: (set(lan), [])),
            patch.object(net.Presence, "probe", probe),
            patch.object(net, "_PROBE_SECONDS", 0),
            patch.object(config, "providers", config.providers._replace(push=push)),
            freeze_time(MONDAY_AFTERNOON) as frozen,
        ):
            house = House(ctx, app.test_client(), frozen, listeners, dispatched, lan, probed, reachable, pushed)
            marker = request.node.get_closest_marker("plugins")
            for name, variant in marker.kwargs.items():
                config.plugin_configs[f"orc_extras/{name}"] = config.plugin_configs[f"orc_extras/{variant}"]
            for plugin in marker.args:
                plugin.setup(ctx)
            for listener in (buttons, battery):
                listener.setup(ctx)
            listeners.append(api._log_external)
            yield house
    finally:
        _load(sample, {})


def _load(config_dir, hub):
    os.environ["ORC_CONFIG_DIR"] = config_dir
    config.load(m.Secrets(), hub)
