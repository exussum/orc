from unittest.mock import patch

import pytest

import orc
from orc import api, config
from orc import model as m
from orc.dal import broker
from orc.dal.mqtt import hubitat
from orc.plugins import battery, buttons


@pytest.fixture
def log_call(ctx):
    with patch.object(ctx.log, "write") as write:
        yield write


@pytest.fixture
def action_run():
    with patch.object(api, "run_action") as run:
        yield run


class TestButtons:
    @staticmethod
    def _wire(ctx, remotes):
        with patch.object(config, "remotes", remotes):
            return _capture("add_status_listener", lambda: buttons.setup(ctx))

    @staticmethod
    def _held(device_id, button=1):
        return m.Status(_remote(device_id), "held", None, button, hubitat.HubitatSource.HUBITAT)

    def test_mapped_event_is_queued_not_run_on_caller_thread(self, ctx, action_run):
        on_button = self._wire(ctx, (m.Remote(orc.Light.a, 1, "held", "TV Lights"),))
        with patch.object(ctx.scheduler, "now") as now:
            on_button(self._held(orc.Light.a.value), TRIGGER)
        action_run.assert_not_called()
        func, payload = now.call_args[0]
        assert payload == ("TV Lights", m.Button(str(orc.Light.a.value), id=TRIGGER.id, log_source=m.LogSource.EXTERNAL))
        assert now.call_args[1]["queue"] == m.Queue.ADHOC
        action_run.return_value = True
        func(payload, ctx=ctx)
        action_run.assert_called_once_with("TV Lights", m.Button(str(orc.Light.a.value), id=TRIGGER.id, log_source=m.LogSource.EXTERNAL))

    def test_unmapped_event_is_ignored(self, ctx, action_run):
        on_button = self._wire(ctx, (m.Remote(orc.Light.a, 1, "held", "TV Lights"),))
        with patch.object(ctx.scheduler, "now") as now:
            on_button(self._held("99"), TRIGGER)
            on_button(self._held(orc.Light.a.value, 2), TRIGGER)
            on_button(m.Status(_remote(orc.Light.a.value), "pushed", None, 1, hubitat.HubitatSource.HUBITAT), TRIGGER)
            on_button(m.Status(_remote(orc.Light.a.value), "switch", "off", "on", hubitat.HubitatSource.HUBITAT), TRIGGER)
        now.assert_not_called()
        action_run.assert_not_called()

    def test_unknown_action_logs(self, ctx, action_run, log_call):
        action_run.return_value = False
        with patch.object(api, "alert"):
            buttons._run_button(("No Such Routine", m.Button("1", log_source=m.LogSource.EXTERNAL)), ctx=ctx)
        log_call.assert_called_once()
        assert "No Such Routine" in log_call.call_args[0][0]


class TestBattery:
    @pytest.mark.parametrize(
        "old, new, expected",
        [("20", "5", True), ("5", "5", False), ("5", "80", False), (None, "5", True)],
    )
    def test_notifies_on_crossing_into_critical(self, ctx, log_call, old, new, expected):
        on_event = _capture("add_status_listener", lambda: battery.setup(ctx))
        device = m.Device("16", "front door", "hubitat")
        on_event(m.Status(device, "battery", old, new, hubitat.HubitatSource.HUBITAT), TRIGGER)
        if expected:
            log_call.assert_called_once_with(
                "Low battery on `front door` (CRITICAL)",
                TRIGGER,
                notification=m.Notification(("battery", "front door"), "/device/"),
            )
        else:
            log_call.assert_not_called()

    def test_ignores_other_attributes(self, ctx, log_call):
        on_event = _capture("add_status_listener", lambda: battery.setup(ctx))
        device = m.Device("16", "front door", "hubitat")
        on_event(m.Status(device, "motion", "inactive", "active", hubitat.HubitatSource.HUBITAT), TRIGGER)
        log_call.assert_not_called()


TRIGGER = m.Broker("16", origin="hubitat", log_source=m.LogSource.EXTERNAL)


def _capture(name, fn):
    captured = {}
    with patch.object(broker, name, side_effect=lambda listener: captured.setdefault("fn", listener)):
        fn()
    return captured["fn"]


def _remote(device_id):
    return m.Device(device_id, "remote", "hubitat")
