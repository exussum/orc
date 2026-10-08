from unittest.mock import patch

import pytest

import orc
from orc import api, config
from orc import model as m
from orc.dal.mqtt import hubitat
from orc.dal.mqtt import paho as mqtt
from orc.plugins import battery, buttons


def _capture(name, fn):
    captured = {}
    with patch.object(mqtt, name, side_effect=lambda listener: captured.setdefault("fn", listener)):
        fn()
    return captured["fn"]


def _remote(device_id):
    return m.Device(device_id, "remote", "hubitat")


class TestButtons:
    @staticmethod
    def _wire(ctx, remotes):
        with patch.object(config, "remotes", remotes):
            return _capture("add_status_listener", lambda: buttons.setup(ctx))

    def test_mapped_event_runs_action(self, ctx):
        on_button = self._wire(ctx, (m.Remote(orc.Light.a, 1, "held", "TV Lights"),))
        with patch.object(api, "run_action", return_value=True) as run:
            on_button(m.Status(_remote(orc.Light.a.value), "held", None, 1, hubitat.HubitatSource.HUBITAT))
        run.assert_called_once_with(ctx, "TV Lights", m.Button(str(orc.Light.a.value)), source=m.LogSource.EXTERNAL)

    def test_unmapped_event_is_ignored(self, ctx):
        on_button = self._wire(ctx, (m.Remote(orc.Light.a, 1, "held", "TV Lights"),))
        with patch.object(api, "run_action") as run:
            on_button(m.Status(_remote("99"), "held", None, 1, hubitat.HubitatSource.HUBITAT))
            on_button(m.Status(_remote(orc.Light.a.value), "held", None, 2, hubitat.HubitatSource.HUBITAT))
            on_button(m.Status(_remote(orc.Light.a.value), "pushed", None, 1, hubitat.HubitatSource.HUBITAT))
            on_button(m.Status(_remote(orc.Light.a.value), "switch", "off", "on", hubitat.HubitatSource.HUBITAT))
        run.assert_not_called()

    def test_unknown_action_logs(self, ctx):
        on_button = self._wire(ctx, (m.Remote(orc.Light.a, 1, "held", "No Such Routine"),))
        with patch.object(api, "run_action", return_value=False), patch.object(api, "log") as log, patch.object(api, "alert"):
            on_button(m.Status(_remote(orc.Light.a.value), "held", None, 1, hubitat.HubitatSource.HUBITAT))
        log.assert_called_once()
        assert "No Such Routine" in log.call_args[0][1]


class TestBattery:
    @pytest.mark.parametrize(
        "old, new, expected",
        [("20", "5", True), ("5", "5", False), ("5", "80", False), (None, "5", True)],
    )
    def test_notifies_on_crossing_into_critical(self, ctx, old, new, expected):
        on_event = _capture("add_status_listener", lambda: battery.setup(ctx))
        device = m.Device("16", "front door", "hubitat")
        with patch.object(api, "log") as log:
            on_event(m.Status(device, "battery", old, new, hubitat.HubitatSource.HUBITAT))
        if expected:
            log.assert_called_once_with(
                m.LogSource.SYSTEM,
                "Low battery on `front door` (CRITICAL)",
                m.Broker(id="16", source="hubitat"),
                notification=m.Notification(("battery", "front door")),
            )
        else:
            log.assert_not_called()

    def test_ignores_other_attributes(self, ctx):
        on_event = _capture("add_status_listener", lambda: battery.setup(ctx))
        device = m.Device("16", "front door", "hubitat")
        with patch.object(api, "log") as log:
            on_event(m.Status(device, "motion", "inactive", "active", hubitat.HubitatSource.HUBITAT))
        log.assert_not_called()
