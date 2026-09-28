from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from freezegun import freeze_time

import orc
from orc import api, config
from orc import model as m
from orc.dal.mqtt import stub as mqtt_stub
from orc.plugins import battery, buttons, external


def _ctx():
    return m.AppContext(scheduler=MagicMock())


def _capture(name, fn):
    captured = {}
    with patch.object(mqtt_stub, name, side_effect=lambda listener: captured.setdefault("fn", listener)):
        fn()
    return captured["fn"]


class TestButtons:
    def _wire(self, remotes):
        ctx = _ctx()
        with patch.object(config, "remotes", remotes):
            return ctx, _capture("add_button_listener", lambda: buttons.setup(ctx))

    def test_mapped_event_runs_action(self):
        ctx, on_button = self._wire((m.Remote(orc.Light.a, 1, "held", "TV Lights"),))
        with patch.object(api, "run_action", return_value=True) as run:
            on_button(orc.Light.a.value, 1, "held")
        run.assert_called_once_with(ctx, "TV Lights", m.Broker(id=str(orc.Light.a.value), source="hubitat"), source=m.LogSource.EXTERNAL)

    def test_unmapped_event_is_ignored(self):
        _, on_button = self._wire((m.Remote(orc.Light.a, 1, "held", "TV Lights"),))
        with patch.object(api, "run_action") as run:
            on_button(99, 1, "held")
            on_button(orc.Light.a.value, 2, "held")
            on_button(orc.Light.a.value, 1, "pushed")
        run.assert_not_called()

    def test_unknown_action_logs(self):
        _, on_button = self._wire((m.Remote(orc.Light.a, 1, "held", "No Such Routine"),))
        with patch.object(api, "run_action", return_value=False), patch.object(api, "log") as log, patch.object(api, "alert"):
            on_button(orc.Light.a.value, 1, "held")
        log.assert_called_once()
        assert "No Such Routine" in log.call_args[0][1]


class TestBattery:
    @pytest.mark.parametrize(
        "old, new, expected",
        [("20", "5", True), ("5", "5", False), ("5", "80", False), ("80", "60", False)],
    )
    def test_notifies_on_crossing_into_critical(self, old, new, expected):
        on_event = _capture("add_listener", lambda: battery.setup(_ctx()))
        device = m.DeviceState(id=16, name="front door", attributes={"battery": new}, last_activity=None)
        with patch.object(api, "log") as log:
            on_event(device, "battery", old, new)
        if expected:
            log.assert_called_once_with(
                m.LogSource.SYSTEM, "Low battery on `front door` (CRITICAL)", m.Broker(id="16", source="hubitat"), should_notify=True
            )
        else:
            log.assert_not_called()

    def test_ignores_other_attributes(self):
        on_event = _capture("add_listener", lambda: battery.setup(_ctx()))
        device = m.DeviceState(id=16, name="front door", attributes={"motion": "active"}, last_activity=None)
        with patch.object(api, "log") as log:
            on_event(device, "motion", "inactive", "active")
        log.assert_not_called()


class TestExternal:
    def test_a_swarm_of_external_changes_rolls_up(self):
        on_external = _capture("add_external_listener", lambda: external.setup(_ctx()))
        api._ACTIVITY_LOG.clear()
        with freeze_time(datetime(2026, 1, 5, 12, tzinfo=config.settings.tz)) as frozen:
            on_external(m.DeviceState(1, "lamp a", {}, None), "switch", "off", "on")
            on_external(m.DeviceState(2, "lamp b", {}, None), "switch", "off", "on")
            frozen.tick(api._ROLLUP_WINDOW)
            on_external(m.DeviceState(1, "lamp a", {}, None), "switch", "on", "off")
        assert [(e.action, [c.action for c in e.children]) for e in api.log_entries()] == [
            ("`lamp a` switch: on → off", []),
            ("`lamp a` switch: off → on", ["`lamp b` switch: off → on"]),
        ]
