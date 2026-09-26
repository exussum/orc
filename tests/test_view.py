from datetime import date, datetime, timedelta
from unittest.mock import ANY, MagicMock, create_autospec, patch

import pytest
from apscheduler.job import Job
from apscheduler.schedulers.base import BaseScheduler
from flask import Flask

import orc
from orc import api, config
from orc import model as m
from orc.dal import scheduler as dal_scheduler
from orc.kernel import engine, loader
from orc.view import VersionManager, bp


def _routine(name, when, *commands, skip_replay=False):
    clauses = tuple(engine.Clause(loader._conditions(c.tag), c) for c in commands)
    tags = frozenset({m.SKIP_REPLAY_TAG}) if skip_replay else frozenset()
    return engine.Rule(engine.At(when), clauses, name=name, tags=tags)


def _room(*commands):
    return engine.Rule(engine.NEVER, tuple(engine.Clause((), c) for c in commands))


@pytest.fixture
def scheduler():
    sched = create_autospec(BaseScheduler, instance=True)
    dal_scheduler.set_scheduler(sched)
    return sched


@pytest.fixture
def ctx(scheduler):
    context = m.AppContext(
        scheduler=scheduler,
        version_manager=VersionManager(),
    )
    api.set_ctx(context)
    return context


@pytest.fixture
def client(ctx):
    app = Flask(__name__, template_folder="../src/orc/templates")
    app.register_blueprint(bp)
    app.orc = ctx
    with app.test_client() as c:
        yield c


@pytest.fixture
def good_version(ctx):
    return {"orc-version": ctx.version_manager.version}


def _fake_job(name="job", next_run_time=True):
    job = create_autospec(Job, instance=True)
    job.id = name
    job.name = name
    job.next_run_time = next_run_time
    return job


# --- VersionManager.versioned decorator ---


def test_versioned_rejects_stale_version(client):
    response = client.post("/api/schedule/set_theme", data={"theme": ""}, headers={"orc-version": "stale"})
    assert response.status_code == 412
    assert "version" in response.get_json()


def test_versioned_bumps_after_success(client, ctx, good_version):
    old = ctx.version_manager.version
    with patch.object(api, "apply_theme_change"):
        response = client.post("/api/schedule/set_theme", data={"theme": ""}, headers=good_version)
    assert response.status_code == 200
    assert ctx.version_manager.version != old


# --- /api/run: 4-way branch ---


def test_console_plugin(client, ctx):
    plugin = m.CallablePlugin(name="do-thing", module=m, func=lambda ctx, device, entry: None)
    with (
        patch.object(config, "plugins", (plugin,)),
        patch("orc.plugins.execute_plugin") as exec_plugin,
    ):
        response = client.get("/api/run/do-thing")
    assert response.status_code == 200
    exec_plugin.assert_called_once_with(ctx, plugin, None, entry=ANY)


def test_console_schedule_routine(client):
    routine = _routine("r", "", engine.Command(m.Devices(orc.Light.a), m.OFF, tag=m.Tag.SYSTEM))
    with (
        patch.object(config, "schedule_routines", {"r": routine}),
        patch.object(config, "plugins", {}),
        patch.object(api, "dispatch") as ex,
    ):
        client.get("/api/run/r")
    ex.assert_called_once_with(m.squish((engine.Command(m.Devices(orc.Light.a), m.OFF, tag=m.Tag.SYSTEM),)), force=True, entry=ANY)


def test_console_ad_hoc(client):
    reset = _routine("reset", "", engine.Command(m.Devices(orc.Light.a), m.OFF))
    routine = m.AdhocAction(engine.Command(m.Devices(orc.Light.b), m.ON))
    with (
        patch.multiple(config, plugins={}, schedule_routines={}, ad_hoc_routines={"r": routine}, reset_config=reset),
        patch.object(api, "dispatch") as ex,
    ):
        client.get("/api/run/r")
    ex.assert_called_once_with((*reset.commands, *routine.commands), force=True, entry=ANY)


def test_console_ad_hoc_no_reset(client):
    routine = m.AdhocAction(engine.Command(m.Devices(orc.Light.b), m.ON), reset=False)
    with (
        patch.multiple(config, plugins={}, schedule_routines={}, ad_hoc_routines={"r": routine}),
        patch.object(api, "dispatch") as ex,
    ):
        client.get("/api/run/r")
    ex.assert_called_once_with(m.squish(routine.commands), force=True, entry=ANY)


def test_button_ad_hoc_snapshot(ctx):
    routine = m.AdhocAction(engine.Command(m.Devices(orc.Light.b), m.ON), snapshot=timedelta(hours=3))
    captured = (engine.Command(m.Devices(orc.Light.a), m.ON),)
    with (
        patch.multiple(config, plugins={}, schedule_routines={}, ad_hoc_routines={"r": routine}),
        patch.object(api, "capture_lights", return_value=captured),
        patch.object(api, "dispatch") as ex,
    ):
        api.run_action(ctx, "r", m.Manual("r"), hub_origin=True)
    snap = ctx.engine.snapshots(api.local_now())[api.ORC_SYSTEM_SNAPSHOT]
    assert snap.routine is captured
    assert snap.end > api.local_now()
    ex.assert_called_once_with(routine.commands, force=True, entry=ANY)


def test_button_ad_hoc_snapshot_does_not_stack(ctx):
    routine = m.AdhocAction(engine.Command(m.Devices(orc.Light.b), m.ON), snapshot=timedelta(hours=3))
    reset = _routine("reset", "", engine.Command(m.Devices(orc.Light.a), m.OFF))
    existing = (engine.Command(m.Devices(orc.Light.a), m.ON),)
    snap = m.SnapShot(routine=existing, end=api.local_now() + timedelta(hours=1))
    ctx.engine.save_snapshot(api.ORC_SYSTEM_SNAPSHOT, snap, snap.end)
    with (
        patch.multiple(config, plugins={}, schedule_routines={}, ad_hoc_routines={"r": routine}, reset_config=reset),
        patch.object(api, "capture_lights") as capture,
        patch.object(api, "dispatch") as ex,
    ):
        api.run_action(ctx, "r", m.Manual("r"), hub_origin=True)
    # Existing snapshot is preserved (not popped, not overwritten) and no new one is taken.
    assert ctx.engine.snapshots(api.local_now())[api.ORC_SYSTEM_SNAPSHOT].routine is existing
    capture.assert_not_called()
    ex.assert_called_once_with((*reset.commands, *routine.commands), force=True, entry=ANY)


def test_console_ad_hoc_snapshot_skipped_for_web_callers(client, ctx):
    routine = m.AdhocAction(engine.Command(m.Devices(orc.Light.b), m.ON), snapshot=timedelta(hours=3))
    reset = _routine("reset", "", engine.Command(m.Devices(orc.Light.a), m.OFF))
    with (
        patch.multiple(config, plugins={}, schedule_routines={}, ad_hoc_routines={"r": routine}, reset_config=reset),
        patch.object(api, "capture_lights") as capture,
        patch.object(api, "dispatch") as ex,
    ):
        client.get("/api/run/r")
    assert api.ORC_SYSTEM_SNAPSHOT not in ctx.engine.snapshots(api.local_now())
    capture.assert_not_called()
    ex.assert_called_once_with((*reset.commands, *routine.commands), force=True, entry=ANY)


def test_console_unknown_returns_404(client):
    with patch.multiple(config, plugins={}, schedule_routines={}, ad_hoc_routines={}):
        response = client.get("/api/run/nope")
    assert response.status_code == 404


# --- /api/room: 4-way branch on state ---


def test_room_on(client):
    with (
        patch.object(config, "rooms", {"Living Room": _room(engine.Command(m.Devices(orc.Light.a), m.ON))}),
        patch.object(api, "dispatch") as ex,
    ):
        client.get("/api/room/Living Room?state=on")
    ex.assert_called_once_with(m.squish((engine.Command(m.Devices(orc.Light.a), m.ON),)), force=True, entry=ANY)


def test_room_off_replaces_state(client):
    with (
        patch.object(config, "rooms", {"Living Room": _room(engine.Command(m.Devices(orc.Light.a), m.ON))}),
        patch.object(api, "dispatch") as ex,
    ):
        client.get("/api/room/Living Room?state=off")
    (cmds,), _ = ex.call_args
    assert all(c.value == m.OFF for c in cmds)


def test_room_follow(client):
    rooms = {
        "Living Room": _room(engine.Command(m.Devices(orc.Light.a), m.ON)),
        "Bedroom": _room(engine.Command(m.Devices(orc.Light.b), m.ON)),
    }
    with (
        patch.object(config, "rooms", rooms),
        patch.object(api, "dispatch") as ex,
    ):
        client.get("/api/room/Living Room?state=follow")
    expected = (
        engine.Command(m.Devices(orc.Light.a), m.OFF),
        engine.Command(m.Devices(orc.Light.b), m.OFF),
        engine.Command(m.Devices(orc.Light.a), m.ON),
    )
    ex.assert_called_once_with(expected, force=True, entry=ANY)


def test_room_unknown_state_raises(client):
    with patch.object(config, "rooms", {"Living Room": ()}):
        response = client.get("/api/room/Living Room?state=bogus")
    assert response.status_code == 500


def test_room_unknown_id_returns_404(client):
    with patch.object(config, "rooms", {}), patch.object(api, "dispatch") as ex:
        response = client.get("/api/room/nope?state=on")
    assert response.status_code == 404
    ex.assert_not_called()


# --- /api/schedule/set_theme: form parsing + conditional date.fromisoformat ---


def test_set_theme_clear_passes_none_dates(client, ctx, good_version):
    with patch.object(api, "apply_theme_change") as apply_change:
        client.post("/api/schedule/set_theme", data={"theme": ""}, headers=good_version)
    apply_change.assert_called_once_with(ctx, "", None, None, m.Manual.THEME)


def test_set_theme_set_parses_dates(client, ctx, good_version):
    theme = next(iter(orc.config.themes))
    with patch.object(api, "apply_theme_change") as apply_change:
        client.post(
            "/api/schedule/set_theme",
            data={"theme": theme, "start": "2100-01-01", "end": "2100-01-10"},
            headers=good_version,
        )
    apply_change.assert_called_once_with(ctx, theme, date(2100, 1, 1), date(2100, 1, 10), m.Manual(theme))


def test_set_theme_rejects_unknown_theme(client, ctx, good_version):
    with patch.object(api, "apply_theme_change") as apply_change:
        response = client.post(
            "/api/schedule/set_theme",
            data={"theme": "vacation", "start": "2100-01-01", "end": "2100-01-10"},
            headers=good_version,
        )
    assert response.status_code == 500
    apply_change.assert_not_called()


# --- /api/durations ---


def test_durations_returns_config(client):
    with patch("orc.api.duration_stats", return_value={"TV Lights": (4, 3.0), "Reset": (2, 0.5)}):
        response = client.get("/api/durations")
    assert response.status_code == 200
    assert response.get_json() == {
        "TV Lights": {"avg": 3.0, "samples": 4, "delay": "0:00:00"},
        "Reset": {"avg": 0.5, "samples": 2, "delay": "0:00:00"},
    }


# --- /api/schedule/<id>/pause: toggles pause/resume ---


def test_pause_when_running_pauses(client, scheduler, good_version):
    job = _fake_job(next_run_time=datetime(2100, 1, 1))
    scheduler.get_job.return_value = job
    client.get("/api/schedule/iot-x/pause", headers=good_version)
    job.pause.assert_called_once()
    job.resume.assert_not_called()


def test_pause_when_paused_resumes(client, scheduler, good_version):
    job = _fake_job(next_run_time=None)
    scheduler.get_job.return_value = job
    client.get("/api/schedule/iot-x/pause", headers=good_version)
    job.resume.assert_called_once()
    job.pause.assert_not_called()


def test_pause_unknown_job_returns_404(client, scheduler, good_version):
    scheduler.get_job.return_value = None
    response = client.get("/api/schedule/nope/pause", headers=good_version)
    assert response.status_code == 404


# --- /schedule/: button colour and badges ---


def _fake_iot_job(name="job", trigger=m.Tag.SYSTEM, run_date=None, skip_replay=False):
    run_date = run_date or datetime(2100, 1, 1)
    rule = _routine(name, "", engine.Command(m.Devices(MagicMock()), "on", tag=trigger), skip_replay=skip_replay)
    job = create_autospec(Job, instance=True)
    job.id = name
    job.name = name
    job.next_run_time = run_date
    job.args = [m.IotJob(rule)]
    job.trigger.run_date = run_date
    return job


def _get_schedule(client, jobs, present_names=()):
    with patch.multiple(
        api,
        fetch_jobs_by_type=MagicMock(return_value=jobs),
        current_theme_override=MagicMock(return_value=None),
        present_names=MagicMock(return_value=set(present_names)),
        fetch_durations=MagicMock(side_effect=lambda mapper=None: mapper([]) if mapper else []),
    ):
        return client.get("/schedule/")


@pytest.mark.parametrize(
    "kwargs, present, present_badges, absent_badges",
    [
        ({"trigger": m.Tag.SYSTEM}, (), (), (b"orc-btn-absent", b"orc-presence-badge", b"orc-skip-replay-badge")),
        ({"trigger": "me"}, (), (b"orc-btn-absent", b"orc-presence-badge"), ()),
        ({"trigger": "me"}, ("me",), (), (b"orc-btn-absent",)),
        ({"trigger": m.Tag.ANYONE}, ("me",), (), (b"orc-btn-absent",)),
        ({"trigger": m.Tag.ANYONE}, (), (b"orc-btn-absent",), ()),
        ({"trigger": m.WeatherCondition.SUNNY}, (), (b"orc-weather-badge",), ()),
        ({"skip_replay": True}, (), (b"orc-skip-replay-badge",), ()),
    ],
    ids=["system", "me_absent", "me_present", "anyone_present", "anyone_absent", "weather", "skip_replay"],
)
def test_schedule_badges(client, kwargs, present, present_badges, absent_badges):
    response = _get_schedule(client, [_fake_iot_job(**kwargs)], present_names=present)
    for badge in present_badges:
        assert badge in response.data
    for badge in absent_badges:
        assert badge not in response.data
