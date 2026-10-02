from datetime import datetime, timedelta

from orc.kernel import engine as e

LIGHT = e.Command("light", "on")
T0 = datetime(2024, 1, 1, 12, 0, 0)


def read_from(world):
    def read(channel):
        return world[channel]

    return read


def test_is_holds_reads_the_world():
    condition = e.Is("ac", "on")
    assert condition.holds(read_from({"ac": "on"}))
    assert not condition.holds(read_from({"ac": "off"}))


def test_in_holds_when_value_is_among_the_reading():
    condition = e.In("weather", "sunny")
    assert condition.holds(read_from({"weather": frozenset({"sunny", "mild"})}))
    assert not condition.holds(read_from({"weather": frozenset({"cloudy"})}))
    assert not condition.holds(read_from({"weather": frozenset()}))


T1 = T0 + timedelta(hours=1)


def test_snapshots_active_until_deadline():
    snaps = e.Runtime()
    snaps.save_snapshot("s", "scene", T1)
    assert snaps.snapshot_active("s", T0) is True
    assert snaps.snapshot_active("s", T1) is True
    assert snaps.snapshot_active("s", T1 + timedelta(seconds=1)) is False
    assert snaps.snapshot_active("missing", T0) is False


def test_snapshots_peek_reads_without_popping():
    snaps = e.Runtime()
    snaps.save_snapshot("s", "scene", T1)
    assert snaps.read_snapshot("s", T0) == "scene"
    assert snaps.read_snapshot("s", T0) == "scene"
    assert snaps.read_snapshot("s", T1 + timedelta(seconds=1)) is None


def test_snapshots_get_pops_live_payload():
    snaps = e.Runtime()
    snaps.save_snapshot("s", "scene", T1)
    assert snaps.pop_snapshot("s", T0) == "scene"
    assert snaps.read_snapshot("s", T0) is None


def test_snapshots_get_expired_returns_none_but_pops():
    snaps = e.Runtime()
    snaps.save_snapshot("s", "scene", T0)
    assert snaps.pop_snapshot("s", T1) is None
    assert snaps.snapshots(T0) == {}


def test_snapshots_lists_only_live():
    snaps = e.Runtime()
    snaps.save_snapshot("live", "a", T1)
    snaps.save_snapshot("dead", "b", T0)
    assert snaps.snapshots(T0 + timedelta(minutes=1)) == {"live": "a"}


def _gate(rt, commands, now, *, force):
    (report,) = rt.evaluate([e.Action(commands)], now, force=force)
    return report.commands


def test_evaluate_keeps_only_rules_whose_condition_holds():
    rt = e.Runtime()
    rule = e.Rule((e.Clause((e.Is("ac", "on"),), LIGHT),))
    assert rt.evaluate([rule], T0, read=read_from({"ac": "on"}), force=True) == (e.Report(rule, (LIGHT,)),)
    assert rt.evaluate([rule], T0, read=read_from({"ac": "off"}), force=True) == (e.Report(rule, ()),)


OPEN = e.Is("door", "open")
CLOSED = e.Is("door", "closed")


def test_a_delayed_automation_defers_and_cancels_on_its_cancel_condition():
    automation = e.Automation(OPEN, e.Rule((e.Clause((), LIGHT),)), delay=timedelta(minutes=5), cancel=CLOSED)
    rt = e.Runtime()
    assert rt.evaluate([automation], T0, read=read_from({"door": "open"}), force=True) == (
        e.Deferred(automation, T0 + timedelta(minutes=5)),
    )
    assert rt.evaluate([automation], T0, read=read_from({"door": "closed"}), force=True) == (e.Cancel(automation),)
    assert rt.evaluate([automation], T0, read=read_from({"door": "ajar"}), force=True) == (e.Report(automation, ()),)


def test_deferred_rechecks_its_conditions_when_run():
    automation = e.Automation(OPEN, e.Rule((e.Clause((e.Is("ac", "on"),), LIGHT),)), delay=timedelta(minutes=5))
    (deferred,) = e.Runtime().evaluate([automation], T0, read=read_from({"door": "open"}), force=True)
    assert e.Runtime().evaluate([deferred], T0, read=read_from({"ac": "off"}), force=True) == (e.Report(deferred, ()),)


def test_cooldown_silences_a_repeat_within_the_window():
    automation = e.Automation(OPEN, e.Rule((e.Clause((), LIGHT),)), cooldown=timedelta(seconds=10))
    rt = e.Runtime()
    read = read_from({"door": "open"})
    assert rt.evaluate([automation], T0, read=read, force=True) == (e.Report(automation, (LIGHT,)),)
    assert rt.evaluate([automation], T0 + timedelta(seconds=5), read=read, force=True) == (e.Report(automation, ()),)
    assert rt.evaluate([automation], T0 + timedelta(seconds=10), read=read, force=True) == (e.Report(automation, (LIGHT,)),)


def test_evaluate_forced_passes_all_without_recording():
    rt = e.Runtime(bypass="SYSTEM", override_key="s")
    rt.save_snapshot("s", e.SnapShot((), T1), T1)
    cmd = e.Command("light", "on", tag="Alice")
    assert _gate(rt, (cmd,), T0, force=True) == (cmd,)
    assert rt.snapshots(T0)["s"].routine == ()


def test_evaluate_passes_all_when_no_snapshot_active():
    cmd = e.Command("light", "on", tag="Alice")
    assert _gate(e.Runtime(bypass="SYSTEM", override_key="s"), (cmd,), T0, force=False) == (cmd,)


def test_evaluate_suppresses_non_bypass_while_override_snapshot_active():
    rt = e.Runtime(bypass="SYSTEM", override_key="s")
    rt.save_snapshot("s", e.SnapShot((), T1), T1)
    cmd = e.Command("light", "on", tag="Alice")
    assert _gate(rt, (cmd,), T0, force=False) == ()


def test_evaluate_ignores_snapshots_under_other_keys():
    rt = e.Runtime(bypass="SYSTEM", override_key="s")
    rt.save_snapshot("entrance_sensor", e.SnapShot((), T1), T1)
    cmd = e.Command("light", "on", tag="Alice")
    assert _gate(rt, (cmd,), T0, force=False) == (cmd,)


def test_evaluate_records_bypass_into_override_snapshot_only():
    rt = e.Runtime(bypass="SYSTEM", override_key="s")
    rt.save_snapshot("s", e.SnapShot((e.Command("light", "off"),), T1), T1)
    rt.save_snapshot("other", e.SnapShot((), T1), T1)
    cmd = e.Command("light", "on", tag="SYSTEM")
    assert _gate(rt, (cmd,), T0, force=False) == (cmd,)
    assert rt.snapshots(T0)["s"].routine == (cmd,)
    assert rt.snapshots(T0)["other"].routine == ()
