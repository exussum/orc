from datetime import datetime, timedelta

import pytest

from orc.kernel import engine as e

LIGHT = e.Command("light", "on")
T0 = datetime(2024, 1, 1, 12, 0, 0)
T1 = T0 + timedelta(hours=1)


def read_from(world):
    def read(channel):
        return world[channel]

    return read


@pytest.fixture
def clock():
    return {e.CLOCK: T0}


@pytest.fixture
def runtime(clock):
    return e.Runtime(read_from(clock))


@pytest.fixture
def overriding_runtime(clock):
    return e.Runtime(read_from(clock), bypass="SYSTEM", override_key="s")


def test_is_holds_reads_the_world():
    condition = e.Is("ac", "on")
    assert condition.holds(read_from({"ac": "on"}))
    assert not condition.holds(read_from({"ac": "off"}))


def test_in_holds_when_value_is_among_the_reading():
    condition = e.In("weather", "sunny")
    assert condition.holds(read_from({"weather": frozenset({"sunny", "mild"})}))
    assert not condition.holds(read_from({"weather": frozenset({"cloudy"})}))
    assert not condition.holds(read_from({"weather": frozenset()}))


def test_snapshots_active_until_deadline(runtime, clock):
    runtime.save_snapshot("s", "scene", T1)
    assert runtime.snapshot_active("s") is True
    assert runtime.snapshot_active("missing") is False
    clock[e.CLOCK] = T1
    assert runtime.snapshot_active("s") is True
    clock[e.CLOCK] = T1 + timedelta(seconds=1)
    assert runtime.snapshot_active("s") is False


def test_snapshots_peek_reads_without_popping(runtime, clock):
    runtime.save_snapshot("s", "scene", T1)
    assert runtime.read_snapshot("s") == "scene"
    assert runtime.read_snapshot("s") == "scene"
    clock[e.CLOCK] = T1 + timedelta(seconds=1)
    assert runtime.read_snapshot("s") is None


def test_snapshots_get_pops_live_payload(runtime):
    runtime.save_snapshot("s", "scene", T1)
    assert runtime.pop_snapshot("s") == "scene"
    assert runtime.read_snapshot("s") is None


def test_snapshots_get_expired_returns_none_but_pops(runtime, clock):
    runtime.save_snapshot("s", "scene", T0)
    clock[e.CLOCK] = T1
    assert runtime.pop_snapshot("s") is None
    assert runtime.snapshots() == {}


def test_snapshots_lists_only_live(runtime, clock):
    runtime.save_snapshot("live", "a", T1)
    runtime.save_snapshot("dead", "b", T0)
    clock[e.CLOCK] = T0 + timedelta(minutes=1)
    assert runtime.snapshots() == {"live": "a"}


def test_a_reader_without_a_clock_is_rejected():
    with pytest.raises(TypeError):
        e.Runtime(lambda _channel: None).snapshots()


def test_evaluate_keeps_only_rules_whose_condition_holds(runtime):
    rule = e.Rule((e.Step((e.Is("ac", "on"),), LIGHT),))
    assert runtime.evaluate([rule], read=read_from({"ac": "on"}), force=True) == (e.Report(rule, (LIGHT,)),)
    assert runtime.evaluate([rule], read=read_from({"ac": "off"}), force=True) == (e.Report(rule, ()),)


OPEN = e.Is("door", "open")
CLOSED = e.Is("door", "closed")


def test_a_delayed_automation_defers_and_cancels_on_its_cancel_condition(runtime):
    automation = e.Automation(OPEN, e.Rule((e.Step((), LIGHT),)), delay=timedelta(minutes=5), cancel=CLOSED)
    assert runtime.evaluate([automation], read=read_from({"door": "open"}), force=True) == (
        e.Deferred(automation, T0 + timedelta(minutes=5)),
    )
    assert runtime.evaluate([automation], read=read_from({"door": "closed"}), force=True) == (e.Cancel(automation),)
    assert runtime.evaluate([automation], read=read_from({"door": "ajar"}), force=True) == (e.Report(automation, ()),)


def test_deferred_rechecks_its_conditions_when_run(runtime):
    automation = e.Automation(OPEN, e.Rule((e.Step((e.Is("ac", "on"),), LIGHT),)), delay=timedelta(minutes=5))
    (deferred,) = runtime.evaluate([automation], read=read_from({"door": "open"}), force=True)
    assert runtime.evaluate([deferred], read=read_from({"ac": "off"}), force=True) == (e.Report(deferred, ()),)


def test_cooldown_silences_a_repeat_within_the_window(runtime, clock):
    automation = e.Automation(OPEN, e.Rule((e.Step((), LIGHT),)), cooldown=timedelta(seconds=10))
    open_door = read_from({"door": "open"})
    assert runtime.evaluate([automation], read=open_door, force=True) == (e.Report(automation, (LIGHT,)),)
    clock[e.CLOCK] = T0 + timedelta(seconds=5)
    assert runtime.evaluate([automation], read=open_door, force=True) == (e.Report(automation, ()),)
    clock[e.CLOCK] = T0 + timedelta(seconds=10)
    assert runtime.evaluate([automation], read=open_door, force=True) == (e.Report(automation, (LIGHT,)),)


def _gate(runtime, commands, *, force):
    (report,) = runtime.evaluate([e.Action(commands)], read=read_from({}), force=force)
    return report.commands


def test_evaluate_forced_passes_all_without_recording(overriding_runtime):
    overriding_runtime.save_snapshot("s", e.SnapShot((), T1), T1)
    cmd = e.Command("light", "on", tag="Alice")
    assert _gate(overriding_runtime, (cmd,), force=True) == (cmd,)
    assert overriding_runtime.snapshots()["s"].routine == ()


def test_evaluate_passes_all_when_no_snapshot_active(overriding_runtime):
    cmd = e.Command("light", "on", tag="Alice")
    assert _gate(overriding_runtime, (cmd,), force=False) == (cmd,)


def test_evaluate_suppresses_non_bypass_while_override_snapshot_active(overriding_runtime):
    overriding_runtime.save_snapshot("s", e.SnapShot((), T1), T1)
    cmd = e.Command("light", "on", tag="Alice")
    assert _gate(overriding_runtime, (cmd,), force=False) == ()


def test_evaluate_ignores_snapshots_under_other_keys(overriding_runtime):
    overriding_runtime.save_snapshot("entrance_sensor", e.SnapShot((), T1), T1)
    cmd = e.Command("light", "on", tag="Alice")
    assert _gate(overriding_runtime, (cmd,), force=False) == (cmd,)


def test_evaluate_records_bypass_into_override_snapshot_only(overriding_runtime):
    overriding_runtime.save_snapshot("s", e.SnapShot((e.Command("light", "off"),), T1), T1)
    overriding_runtime.save_snapshot("other", e.SnapShot((), T1), T1)
    cmd = e.Command("light", "on", tag="SYSTEM")
    assert _gate(overriding_runtime, (cmd,), force=False) == (cmd,)
    assert overriding_runtime.snapshots()["s"].routine == (cmd,)
    assert overriding_runtime.snapshots()["other"].routine == ()
