from datetime import datetime
from unittest.mock import patch

import pytest
from freezegun import freeze_time

import orc
from orc import api, config
from orc import model as m
from orc.dal.mqtt import paho as mqtt
from orc.plugins import battery, buttons, external


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
            return _capture("add_listener", lambda: buttons.setup(ctx))

    def test_mapped_event_runs_action(self, ctx):
        on_button = self._wire(ctx, (m.Remote(orc.Light.a, 1, "held", "TV Lights"),))
        with patch.object(api, "run_action", return_value=True) as run:
            on_button(_remote(orc.Light.a.value), "held", None, 1)
        run.assert_called_once_with(ctx, "TV Lights", m.Button(str(orc.Light.a.value)), source=m.LogSource.EXTERNAL)

    def test_unmapped_event_is_ignored(self, ctx):
        on_button = self._wire(ctx, (m.Remote(orc.Light.a, 1, "held", "TV Lights"),))
        with patch.object(api, "run_action") as run:
            on_button(_remote("99"), "held", None, 1)
            on_button(_remote(orc.Light.a.value), "held", None, 2)
            on_button(_remote(orc.Light.a.value), "pushed", None, 1)
            on_button(_remote(orc.Light.a.value), "switch", "off", "on")
        run.assert_not_called()

    def test_unknown_action_logs(self, ctx):
        on_button = self._wire(ctx, (m.Remote(orc.Light.a, 1, "held", "No Such Routine"),))
        with patch.object(api, "run_action", return_value=False), patch.object(api, "log") as log, patch.object(api, "alert"):
            on_button(_remote(orc.Light.a.value), "held", None, 1)
        log.assert_called_once()
        assert "No Such Routine" in log.call_args[0][1]


class TestBattery:
    @pytest.mark.parametrize(
        "old, new, expected",
        [("20", "5", True), ("5", "5", False), ("5", "80", False), (None, "5", True)],
    )
    def test_notifies_on_crossing_into_critical(self, ctx, old, new, expected):
        on_event = _capture("add_listener", lambda: battery.setup(ctx))
        device = m.Device("16", "front door", "hubitat")
        with patch.object(api, "log") as log:
            on_event(device, "battery", old, new)
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
        on_event = _capture("add_listener", lambda: battery.setup(ctx))
        device = m.Device("16", "front door", "hubitat")
        with patch.object(api, "log") as log:
            on_event(device, "motion", "inactive", "active")
        log.assert_not_called()


class TestExternal:
    def test_a_swarm_of_external_changes_rolls_up(self, ctx):
        on_external = _capture("add_external_listener", lambda: external.setup(ctx))
        api._ACTIVITY_LOG.clear()
        with freeze_time(datetime(2026, 1, 5, 12, tzinfo=config.settings.tz)) as frozen:
            on_external(m.Device("1", "lamp a", "hubitat"), "switch", "off", "on")
            on_external(m.Device("2", "lamp b", "hubitat"), "switch", "off", "on")
            frozen.tick(api._ROLLUP_WINDOW)
            on_external(m.Device("1", "lamp a", "hubitat"), "switch", "on", "off")
        assert [(e.action, [c.action for c in e.children]) for e in api.log_entries()] == [
            ("`lamp a` switch: on → off", []),
            ("`lamp a` switch: off → on", ["`lamp b` switch: off → on"]),
        ]
