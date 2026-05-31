from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

import orc
from orc import api, config
from orc import model as m
from orc.view import VersionManager, bp


@pytest.fixture
def scheduler():
    return MagicMock()


@pytest.fixture
def ctx(scheduler):
    return m.AppContext(
        config_manager=api.ConfigManager(),
        scheduler=scheduler,
        sound_path="/tmp/alert.mp3",
        version_manager=VersionManager(),
    )


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
    job = MagicMock()
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


# --- /api/remote: branches on id ---


def test_remote_tv_lights(client, ctx):
    with patch.object(api, "replace_config_for") as rcf:
        response = client.get("/api/remote/TV Lights")
    assert response.status_code == 200
    rcf.assert_called_once_with(ctx.config_manager, "TV Lights", timedelta(hours=3))


def test_remote_other_resumes(client, ctx):
    fake_cfg = object()
    with (
        patch.object(config, "all_configs", {"Silence": fake_cfg}),
        patch.object(ctx.config_manager, "resume") as resume,
    ):
        client.get("/api/remote/Silence")
    resume.assert_called_once_with(fake_cfg)


# --- /api/console: 4-way branch ---


def test_console_plugin(client, ctx):
    with (
        patch.object(config, "plugins", {"do-thing": "fn"}),
        patch("orc.view.plugins.execute_plugin") as exec_plugin,
    ):
        response = client.get("/api/console/do-thing")
    assert response.status_code == 200
    exec_plugin.assert_called_once_with(ctx.config_manager, "do-thing")


def test_console_schedule_routine(client):
    routine = m.Routine("r", "", ())
    with (
        patch.object(config, "schedule_routines", {"r": routine}),
        patch.object(config, "plugins", {}),
        patch.object(api, "execute") as ex,
    ):
        client.get("/api/console/r")
    ex.assert_called_once_with(routine)


def test_console_ad_hoc(client):
    reset = m.Configs(m.Config(orc.Light.a, config.OFF))
    routine = m.Configs(m.Config(orc.Light.b, config.ON))
    with (
        patch.object(config, "plugins", {}),
        patch.object(config, "schedule_routines", {}),
        patch.object(config, "ad_hoc_routines", {"r": routine}),
        patch.object(config, "reset_config", reset),
        patch.object(api, "execute") as ex,
    ):
        client.get("/api/console/r")
    ex.assert_called_once_with(m.squish_configs(reset, routine))


def test_console_unknown_raises(client):
    with (
        patch.object(config, "plugins", {}),
        patch.object(config, "schedule_routines", {}),
        patch.object(config, "ad_hoc_routines", {}),
    ):
        response = client.get("/api/console/nope")
    assert response.status_code == 500


# --- /api/room: 4-way branch on state ---


def test_room_on(client):
    routine = m.Configs(m.Config(orc.Light.a, config.ON))
    with (
        patch.object(config, "room_configs", {"Living Room": routine}),
        patch.object(api, "execute") as ex,
    ):
        client.get("/api/room/Living Room?state=on")
    ex.assert_called_once_with(routine)


def test_room_off_replaces_state(client):
    routine = m.Configs(m.Config(orc.Light.a, config.ON))
    with (
        patch.object(config, "room_configs", {"Living Room": routine}),
        patch.object(api, "execute") as ex,
    ):
        client.get("/api/room/Living Room?state=off")
    (args,), _ = ex.call_args
    assert all(c.state == config.OFF for c in args.items)


def test_room_follow(client):
    routine = m.Configs(m.Config(orc.Light.a, config.ON))
    off = m.Configs(m.Config(orc.Light.b, config.OFF))
    with (
        patch.object(config, "room_configs", {"Living Room": routine}),
        patch.object(config, "room_configs_off", off),
        patch.object(api, "execute") as ex,
    ):
        client.get("/api/room/Living Room?state=follow")
    ex.assert_called_once_with(m.squish_configs(off, routine))


def test_room_unknown_state_raises(client):
    with patch.object(config, "room_configs", {"Living Room": m.Configs()}):
        response = client.get("/api/room/Living Room?state=bogus")
    assert response.status_code == 500


# --- /api/schedule/set_theme: form parsing + conditional date.fromisoformat ---


def test_set_theme_clear_passes_none_dates(client, ctx, good_version):
    with patch.object(api, "apply_theme_change") as apply_change:
        client.post("/api/schedule/set_theme", data={"theme": ""}, headers=good_version)
    apply_change.assert_called_once_with(ctx, "", None, None)


def test_set_theme_set_parses_dates(client, ctx, good_version):
    with patch.object(api, "apply_theme_change") as apply_change:
        client.post(
            "/api/schedule/set_theme",
            data={"theme": "vacation", "start": "2100-01-01", "end": "2100-01-10"},
            headers=good_version,
        )
    apply_change.assert_called_once_with(ctx, "vacation", date(2100, 1, 1), date(2100, 1, 10))


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
