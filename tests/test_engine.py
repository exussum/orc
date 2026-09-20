from datetime import datetime, timedelta

from orc.kernel import engine as e

LIGHT = e.Command("light", "on")
T0 = datetime(2024, 1, 1, 12, 0, 0)
DOOR_OPEN = e.Event("door", "closed", "open")


def read_from(world):
    def read(channel):
        return world[channel]

    return read


def test_transition_fired_on_matching_channel_and_value():
    assert e.Transition("door", "open").fired(e.Event("door", "closed", "open"))


def test_transition_ignores_other_channel_and_other_value():
    trigger = e.Transition("door", "open")
    assert not trigger.fired(e.Event("window", "closed", "open"))
    assert not trigger.fired(e.Event("door", "open", "closed"))


def test_changed_fires_on_any_listed_channel_regardless_of_value():
    trigger = e.Changed(("temp", "humidity"))
    assert trigger.fired(e.Event("temp", 70, 71))
    assert trigger.fired(e.Event("humidity", 50, 50))
    assert not trigger.fired(e.Event("battery", 90, 89))


def test_is_holds_reads_the_world():
    condition = e.Is("ac", "on")
    assert condition.holds(read_from({"ac": "on"}))
    assert not condition.holds(read_from({"ac": "off"}))


def test_in_holds_when_value_is_among_the_reading():
    condition = e.In("weather", "sunny")
    assert condition.holds(read_from({"weather": frozenset({"sunny", "mild"})}))
    assert not condition.holds(read_from({"weather": frozenset({"cloudy"})}))
    assert not condition.holds(read_from({"weather": frozenset()}))


def test_empty_conditions_fire_without_reading():
    def read(channel):
        raise AssertionError("empty conditions must not call read")

    rule = e.Rule(e.Transition("door", "open"), (e.Clause((), LIGHT),))
    assert e.Runtime([rule]).on_event(DOOR_OPEN, T0, read) == (e.Report(hash(rule), e.Disposition.FIRED),)


def test_runtime_immediate_rule_reports_fired():
    rule = e.Rule(e.Transition("door", "open"), (e.Clause((), LIGHT),))
    reaction = e.Runtime([rule]).on_event(DOOR_OPEN, T0, read_from({}))
    assert reaction == (e.Report(hash(rule), e.Disposition.FIRED),)


def test_runtime_cooldown_reports_cooled_with_elapsed():
    rt = e.Runtime([e.Rule(e.Transition("door", "open"), (e.Clause((), LIGHT),), cooldown=timedelta(seconds=10))])
    assert rt.on_event(DOOR_OPEN, T0, read_from({}))[0].disposition is e.Disposition.FIRED
    cooled = rt.on_event(DOOR_OPEN, T0 + timedelta(seconds=5), read_from({}))[0]
    assert cooled.disposition is e.Disposition.COOLED
    assert cooled.since == timedelta(seconds=5)
    assert rt.on_event(DOOR_OPEN, T0 + timedelta(seconds=11), read_from({}))[0].disposition is e.Disposition.FIRED


def test_runtime_condition_failure_reports_blocked():
    rt = e.Runtime([e.Rule(e.Transition("door", "open"), (e.Clause((e.Is("ac", "on"),), LIGHT),))])
    assert rt.on_event(DOOR_OPEN, T0, read_from({"ac": "off"}))[0].disposition is e.Disposition.BLOCKED


def test_runtime_delayed_rule_defers_without_reporting():
    rule = e.Rule(e.Transition("door", "open"), (e.Clause((), LIGHT),), timedelta(minutes=5))
    reaction = e.Runtime([rule]).on_event(DOOR_OPEN, T0, read_from({}))
    assert reaction == (e.Deferred(hash(rule), rule, T0 + timedelta(minutes=5)),)


def test_runtime_on_fire_reports_fired():
    rule = e.Rule(e.Transition("door", "open"), (e.Clause((), LIGHT),), timedelta(minutes=5))
    rt = e.Runtime([rule])
    (deferred,) = rt.on_event(DOOR_OPEN, T0, read_from({}))
    assert rt.on_fire(deferred, T0 + timedelta(minutes=5), read_from({})) == e.Report(hash(rule), e.Disposition.FIRED)


def test_runtime_reverse_edge_cancels_pending():
    rule = e.Rule(e.Transition("door", "open"), (e.Clause((), LIGHT),), timedelta(minutes=5))
    rt = e.Runtime([rule])
    assert rt.on_event(e.Event("door", "open", "closed"), T0, read_from({})) == (e.Cancel(hash(rule)),)


def test_runtime_on_fire_rechecks_condition():
    rule = e.Rule(e.Transition("door", "open"), (e.Clause((e.Is("ac", "on"),), LIGHT),), timedelta(minutes=5))
    rt = e.Runtime([rule])
    (deferred,) = rt.on_event(DOOR_OPEN, T0, read_from({}))
    fired = rt.on_fire(deferred, T0 + timedelta(minutes=5), read_from({"ac": "off"}))
    assert fired.disposition is e.Disposition.BLOCKED


T1 = T0 + timedelta(hours=1)


def test_snapshots_active_until_deadline():
    snaps = e.Runtime([])
    snaps.save_snapshot("s", "scene", T1)
    assert snaps.snapshot_active("s", T0) is True
    assert snaps.snapshot_active("s", T1) is True
    assert snaps.snapshot_active("s", T1 + timedelta(seconds=1)) is False
    assert snaps.snapshot_active("missing", T0) is False


def test_snapshots_peek_reads_without_popping():
    snaps = e.Runtime([])
    snaps.save_snapshot("s", "scene", T1)
    assert snaps.read_snapshot("s", T0) == "scene"
    assert snaps.read_snapshot("s", T0) == "scene"
    assert snaps.read_snapshot("s", T1 + timedelta(seconds=1)) is None


def test_snapshots_get_pops_live_payload():
    snaps = e.Runtime([])
    snaps.save_snapshot("s", "scene", T1)
    assert snaps.take_snapshot("s", T0) == "scene"
    assert snaps.read_snapshot("s", T0) is None


def test_snapshots_get_expired_returns_none_but_pops():
    snaps = e.Runtime([])
    snaps.save_snapshot("s", "scene", T0)
    assert snaps.take_snapshot("s", T1) is None
    assert snaps.snapshots(T0) == {}


def test_snapshots_lists_only_live():
    snaps = e.Runtime([])
    snaps.save_snapshot("live", "a", T1)
    snaps.save_snapshot("dead", "b", T0)
    assert snaps.snapshots(T0 + timedelta(minutes=1)) == {"live": "a"}


def _gate(rt, commands, now, *, force):
    rules = [e.Rule(e.NEVER, (e.Clause((), c),)) for c in commands]
    return rt.evaluate(rules, now, force=force)


def test_evaluate_keeps_only_rules_whose_condition_holds():
    rt = e.Runtime([])
    rule = e.Rule(e.NEVER, (e.Clause((e.Is("ac", "on"),), LIGHT),))
    assert rt.evaluate([rule], T0, read=read_from({"ac": "on"}), force=True) == (LIGHT,)
    assert rt.evaluate([rule], T0, read=read_from({"ac": "off"}), force=True) == ()


def test_evaluate_forced_passes_all_without_recording():
    rt = e.Runtime([], bypass="SYSTEM", override_key="s")
    rt.save_snapshot("s", e.SnapShot((), T1), T1)
    cmd = e.Command("light", "on", tag="Alice")
    assert _gate(rt, (cmd,), T0, force=True) == (cmd,)
    assert rt.snapshots(T0)["s"].routine == ()


def test_evaluate_passes_all_when_no_snapshot_active():
    cmd = e.Command("light", "on", tag="Alice")
    assert _gate(e.Runtime([], bypass="SYSTEM", override_key="s"), (cmd,), T0, force=False) == (cmd,)


def test_evaluate_suppresses_non_bypass_while_override_snapshot_active():
    rt = e.Runtime([], bypass="SYSTEM", override_key="s")
    rt.save_snapshot("s", e.SnapShot((), T1), T1)
    cmd = e.Command("light", "on", tag="Alice")
    assert _gate(rt, (cmd,), T0, force=False) == ()


def test_evaluate_ignores_snapshots_under_other_keys():
    rt = e.Runtime([], bypass="SYSTEM", override_key="s")
    rt.save_snapshot("entrance_sensor", e.SnapShot((), T1), T1)
    cmd = e.Command("light", "on", tag="Alice")
    assert _gate(rt, (cmd,), T0, force=False) == (cmd,)


def test_evaluate_records_bypass_into_override_snapshot_only():
    rt = e.Runtime([], bypass="SYSTEM", override_key="s")
    rt.save_snapshot("s", e.SnapShot((e.Command("light", "off"),), T1), T1)
    rt.save_snapshot("other", e.SnapShot((), T1), T1)
    cmd = e.Command("light", "on", tag="SYSTEM")
    assert _gate(rt, (cmd,), T0, force=False) == (cmd,)
    assert rt.snapshots(T0)["s"].routine == (cmd,)
    assert rt.snapshots(T0)["other"].routine == ()
