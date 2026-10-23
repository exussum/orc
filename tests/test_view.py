from datetime import date, datetime, timedelta
from unittest.mock import ANY, MagicMock, create_autospec, patch

import pytest
from apscheduler.job import Job
from flask import Flask
from freezegun import freeze_time
from orc_engine import model as em

import orc
from orc import api, config
from orc import model as m
from orc.view import bp

MAC = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:130.0) Gecko/20100101 Firefox/130.0"}
ANDROID = {"User-Agent": "Mozilla/5.0 (Linux; Android 14) Chrome/128.0 Mobile Safari/537.36"}


@pytest.fixture
def config_rooms(monkeypatch):
    def install(payload):
        monkeypatch.setattr(config, "rooms", payload)

    return install


@pytest.fixture
def fake_theme_change():
    with patch.object(api, "apply_theme_change") as apply_change:
        yield apply_change


@pytest.fixture
def fake_duration_stats():
    with patch("orc.api.duration_stats", return_value={}) as stats:
        yield stats


@pytest.fixture
def flask_client(ctx):
    app = Flask(__name__, template_folder="../src/orc/templates")
    app.register_blueprint(bp)
    app.orc = ctx
    with app.test_client() as c:
        yield c


@pytest.mark.parametrize(
    ("host", "title"),
    [("orc.internal.example", "Orc"), ("orc.internal.example:8000", "Orc"), ("orc.example", "Orc Remote")],
    ids=["internal", "internal-port", "remote"],
)
def test_manifest_titles_the_remote_host(flask_client, host, title):
    manifest = flask_client.get("/manifest.json", headers={"Host": host}).json
    assert (manifest["name"], manifest["short_name"]) == (title, title)


def test_console_plugin(flask_client, ctx):
    plugin = m.CallablePlugin(name="do-thing", module=m, func=lambda ctx, device, entry: None)
    with (
        patch.object(config, "plugins", (plugin,)),
        patch("orc.plugins.execute_plugin") as exec_plugin,
    ):
        response = flask_client.get("/api/run/do-thing")
    assert response.status_code == 200
    exec_plugin.assert_called_once_with(ctx, plugin, None, entry=ANY)


def test_console_schedule_routine(flask_client, command_dispatch):
    rule = _routine("r", "", em.Command(m.Devices(orc.Light.a), m.OFF, tag=m.Always()))
    with patch.object(config, "schedule_routines", {"r": rule}):
        flask_client.get("/api/run/r")
    command_dispatch.assert_called_once_with(m.squish((em.Command(m.Devices(orc.Light.a), m.OFF, tag=m.Always()),)), force=True, entry=ANY)


def test_console_ad_hoc(flask_client, config_ad_hoc, command_dispatch):
    reset = _routine("reset", "", em.Command(m.Devices(orc.Light.a), m.OFF))
    action = m.AdhocAction((em.Command(m.Devices(orc.Light.b), m.ON),))
    with config_ad_hoc({"r": action}, reset_config=reset):
        flask_client.get("/api/run/r")
    command_dispatch.assert_called_once_with((*reset.commands, *action.commands), force=True, entry=ANY)


def test_console_ad_hoc_no_reset(flask_client, config_ad_hoc, command_dispatch):
    action = m.AdhocAction((em.Command(m.Devices(orc.Light.b), m.ON),), reset=False)
    with config_ad_hoc({"r": action}):
        flask_client.get("/api/run/r")
    command_dispatch.assert_called_once_with(m.squish(action.commands), force=True, entry=ANY)


def test_delayed_ad_hoc_nests_its_run_under_the_queued_entry(flask_client, ctx, config_ad_hoc, command_dispatch):
    action = m.AdhocAction((em.Command(m.Devices(orc.Light.b), m.ON),), name="r", reset=False, delay=timedelta(minutes=7))
    with config_ad_hoc({"r": action}), freeze_time(api.local_now()) as frozen:
        flask_client.get("/api/run/r")
        frozen.tick(timedelta(minutes=7))
        ctx.scheduler.once.call_args.args[0](ctx=ctx)
    (queued,) = api.log_entries()
    assert [c.action for c in queued.children] == ["Running `r`"]
    command_dispatch.assert_called_once_with(action.commands, force=True, entry=queued)


def test_console_ad_hoc_snapshot_skipped_for_web_callers(flask_client, ctx, config_ad_hoc, command_dispatch, fake_capture_lights):
    action = m.AdhocAction((em.Command(m.Devices(orc.Light.b), m.ON),), snapshot=timedelta(hours=3))
    reset = _routine("reset", "", em.Command(m.Devices(orc.Light.a), m.OFF))
    with config_ad_hoc({"r": action}, reset_config=reset):
        flask_client.get("/api/run/r")
    assert api.ORC_SYSTEM_SNAPSHOT not in ctx.engine.snapshots()
    fake_capture_lights.assert_not_called()
    command_dispatch.assert_called_once_with((*reset.commands, *action.commands), force=True, entry=ANY)


def test_console_unknown_returns_404(flask_client, config_ad_hoc):
    with config_ad_hoc({}):
        response = flask_client.get("/api/run/nope")
    assert response.status_code == 404


def test_room_on(flask_client, config_rooms, command_dispatch):
    config_rooms({"Living Room": _room(em.Command(m.Devices(orc.Light.a), m.ON))})
    flask_client.get("/api/room/Living Room?state=on")
    command_dispatch.assert_called_once_with(m.squish((em.Command(m.Devices(orc.Light.a), m.ON),)), force=True, entry=ANY)


def test_room_off_turns_all_off(flask_client, config_rooms, command_dispatch):
    config_rooms({"Living Room": _room(em.Command(m.Devices(orc.Light.a), m.ON))})
    flask_client.get("/api/room/Living Room?state=off")
    (cmds,), _ = command_dispatch.call_args
    assert all(c.value == m.OFF for c in cmds)


def test_room_follow(flask_client, config_rooms, command_dispatch):
    config_rooms(
        {
            "Living Room": _room(em.Command(m.Devices(orc.Light.a), m.ON)),
            "Bedroom": _room(em.Command(m.Devices(orc.Light.b), m.ON)),
        }
    )
    flask_client.get("/api/room/Living Room?state=follow")
    expected = (
        em.Command(m.Devices(orc.Light.a), m.OFF),
        em.Command(m.Devices(orc.Light.b), m.OFF),
        em.Command(m.Devices(orc.Light.a), m.ON),
    )
    command_dispatch.assert_called_once_with(expected, force=True, entry=ANY)


def test_room_unknown_state_returns_400(flask_client, config_rooms):
    config_rooms({"Living Room": ()})
    response = flask_client.get("/api/room/Living Room?state=bogus")
    assert (response.status_code, response.get_json()) == (400, {"error": "Unknown room state: bogus"})


def test_room_unknown_id_returns_404(flask_client, config_rooms, command_dispatch):
    config_rooms({})
    response = flask_client.get("/api/room/nope?state=on")
    assert response.status_code == 404
    command_dispatch.assert_not_called()


def test_set_theme_clear_passes_none_dates(flask_client, ctx, fake_theme_change):
    flask_client.post("/api/schedule/theme", data={"theme": ""})
    fake_theme_change.assert_called_once_with(ctx, "", None, None, m.Manual("theme"))


def test_set_theme_set_parses_dates(flask_client, ctx, fake_theme_change):
    theme = next(iter(orc.config.themes))
    flask_client.post("/api/schedule/theme", data={"theme": theme, "start": "2100-01-01", "end": "2100-01-10"})
    fake_theme_change.assert_called_once_with(ctx, theme, date(2100, 1, 1), date(2100, 1, 10), m.Manual(theme))


def test_set_theme_rejects_unknown_theme(flask_client, ctx, fake_theme_change):
    response = flask_client.post("/api/schedule/theme", data={"theme": "vacation", "start": "2100-01-01", "end": "2100-01-10"})
    assert response.status_code == 404
    fake_theme_change.assert_not_called()


def test_durations_returns_stats(flask_client, fake_duration_stats):
    fake_duration_stats.return_value = {"TV Lights": (4, 3.0), "Reset": (2, 0.5)}
    with patch("orc.api.action_delays", return_value={}):
        response = flask_client.get("/api/durations")
    assert response.status_code == 200
    assert response.get_json() == {
        "TV Lights": {"avg": 3.0, "samples": 4, "delay": "0:00:00"},
        "Reset": {"avg": 0.5, "samples": 2, "delay": "0:00:00"},
    }


def test_durations_lists_a_delayed_action_that_never_ran(flask_client, config_ad_hoc, fake_duration_stats):
    dog = m.AdhocAction((em.Command(m.Devices(orc.Light.b), m.ON),), name="Dog", delay=timedelta(minutes=6))
    with config_ad_hoc({"AD_HOC_DOG": dog}):
        response = flask_client.get("/api/durations")
    assert response.get_json() == {"AD_HOC_DOG": {"avg": 0.0, "samples": 0, "delay": "0:06:00"}}


def test_actions_lists_what_arrow_can_call(flask_client, config_ad_hoc, config_rooms, fake_duration_stats):
    dog = m.AdhocAction((em.Command(m.Devices(orc.Light.b), m.ON),), name="Dog", delay=timedelta(minutes=6))
    config_rooms({"Living Room": _room(em.Command(m.Devices(orc.Light.a), m.ON))})
    fake_duration_stats.return_value = {"Living Room": (4, 3.0)}
    with config_ad_hoc({"AD_HOC_DOG": dog}):
        response = flask_client.get("/api/actions")
    assert response.status_code == 200
    body = response.get_json()
    assert body["rooms"] == {"Living Room": {"avg": 3.0, "samples": 4, "delay": "0:00:00"}}
    assert body["routines"]["AD_HOC_DOG"] == {"name": "Dog", "avg": 0.0, "samples": 0, "delay": "0:06:00"}
    assert "people" in body and "plugins" in body


@pytest.mark.parametrize("paused", [True, False])
def test_pause_sets_the_absolute_state(flask_client, ctx, paused):
    ctx.scheduler.set_paused.return_value = True
    flask_client.get(f"/api/schedule/iot-x/pause?paused={int(paused)}")
    ctx.scheduler.set_paused.assert_called_once_with("iot-x", paused)


def test_pause_unknown_job_returns_404(flask_client, ctx):
    ctx.scheduler.set_paused.return_value = False
    response = flask_client.get("/api/schedule/nope/pause?paused=1")
    assert response.status_code == 404


def test_device_missing_state_returns_400(flask_client):
    response = flask_client.get("/api/device/nope")
    assert (response.status_code, response.get_json()) == (400, {"error": "Missing state"})


def test_checkin_unknown_person_returns_404(flask_client):
    response = flask_client.get("/api/presence/nobody/checkin")
    assert (response.status_code, response.get_json()) == (404, {"error": "Unknown person: nobody"})


def test_device_unknown_returns_404(flask_client):
    response = flask_client.get("/api/device/nope?state=on")
    assert response.status_code == 404


@pytest.mark.parametrize(
    "kwargs, present, present_badges, absent_badges",
    [
        ({"trigger": m.Always()}, (), (), (b"orc-btn-muted orc-runner", "👤".encode(), "⏩".encode())),
        ({"trigger": m.Present("me")}, (), (b"orc-btn-muted orc-runner", "👤".encode()), ()),
        ({"trigger": m.Present("me")}, ("me",), (), (b"orc-btn-muted orc-runner",)),
        ({"trigger": m.Anyone()}, ("me",), (), (b"orc-btn-muted orc-runner",)),
        ({"trigger": m.Anyone()}, (), (b"orc-btn-muted orc-runner",), ()),
        ({"trigger": m.Weather(m.WeatherCondition.SUNNY)}, (), ("🌤".encode(),), ()),
        ({"skip_replay": True}, (), ("⏩".encode(),), ()),
    ],
    ids=["system", "me_absent", "me_present", "anyone_present", "anyone_absent", "weather", "skip_replay"],
)
def test_schedule_badges(flask_client, kwargs, present, present_badges, absent_badges):
    response = _get_schedule(flask_client, [_fake_iot_job(**kwargs)], present_names=present)
    for badge in present_badges:
        assert badge in response.data
    for badge in absent_badges:
        assert badge not in response.data


def test_log_page_bells_a_notifying_line_and_its_parent(flask_client):
    trigger = m.Manual("bell")
    api.log(m.LogSource.SYSTEM, "Walk in", trigger)
    api.log(m.LogSource.SYSTEM, "Nobody home", trigger, notification=m.Notification(("entrance",)))
    html = flask_client.get("/log/").get_data(as_text=True)
    assert html.count("🔔") == 2


def test_log_page_links_a_line_before_its_action(flask_client):
    api.log(m.LogSource.SYSTEM, "Standup", m.Manual("link"), link="/api/calendar/choose/?title=a")
    html = flask_client.get("/log/").get_data(as_text=True)
    assert (
        '<a href="/api/calendar/choose/?title=a">🔗</a>\n                            <div class="orc-log-action truncate min-w-0">Standup'
        in html
    )


def test_system_page_adds_dewpoint_to_sensors(flask_client):
    status = m.DeviceStatus(name="living room sensor", details={"temperature": 77, "humidity": "60"})
    with patch.object(api, "capture_sensors", return_value=[status]):
        html = flask_client.get("/system/").get_data(as_text=True)
    assert "dewpoint" in html and "62.0" in html


def test_system_page_lists_each_light(flask_client):
    response = flask_client.get("/system/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert all(f">{light.name}</td>" in html for light in config.devices.Light)


def test_push_open_from_a_mac_hands_a_meet_link_to_chrome(flask_client):
    response = flask_client.get("/api/push/open", query_string={"url": "https://meet.google.com/abc?x=1"}, headers=MAC)
    assert response.status_code == 302
    assert response.headers["Location"] == "orc-chrome://meet.google.com/abc?x=1"


def test_push_open_elsewhere_redirects_to_the_meet_link(flask_client):
    response = flask_client.get("/api/push/open", query_string={"url": "https://meet.google.com/abc"}, headers=ANDROID)
    assert response.status_code == 302
    assert response.headers["Location"] == "https://meet.google.com/abc"


@pytest.mark.parametrize("url", ["https://us02web.zoom.us/j/123", "https://teams.microsoft.com/l/meetup-join/abc"])
@pytest.mark.parametrize("headers", [MAC, ANDROID])
def test_push_open_redirects_zoom_and_teams_everywhere(flask_client, url, headers):
    response = flask_client.get("/api/push/open", query_string={"url": url}, headers=headers)
    assert response.status_code == 302
    assert response.headers["Location"] == url


@pytest.mark.parametrize(
    "url",
    [
        "",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://evil.example/",
        "https://meet.google.com.evil.example/abc",
        "https://evilzoom.us/j/1",
        "https://evil.example/?u=https://meet.google.com/abc",
    ],
)
def test_push_open_rejects_anything_but_meeting_links(flask_client, url):
    response = flask_client.get("/api/push/open", query_string={"url": url}, headers=MAC)
    assert response.status_code == 400


def _routine(name, when, *commands, skip_replay=False):
    steps = tuple(em.Step(c.tag or em.And(), c) for c in commands)
    tags = frozenset({m.SKIP_REPLAY_TAG}) if skip_replay else frozenset()
    return em.Rule(steps, name=name, tags=tags)


def _room(*commands):
    return em.Action(commands)


def _fake_iot_job(name="job", trigger=None, run_date=None, skip_replay=False):
    run_date = run_date or datetime(2100, 1, 1)
    rule = _routine(name, "", em.Command(m.Devices(MagicMock()), "on", tag=trigger or m.Always()), skip_replay=skip_replay)
    job = create_autospec(Job, instance=True)
    job.id = name
    job.name = name
    job.next_run_time = run_date
    job.args = [m.IotJob(rule, name)]
    job.trigger.run_date = run_date
    return job


def _get_schedule(flask_client, jobs, present_names=()):
    with patch.multiple(
        api,
        fetch_jobs_by_type=MagicMock(return_value=jobs),
        current_theme_override=MagicMock(return_value=None),
        present_names=MagicMock(return_value=set(present_names)),
        fetch_durations=MagicMock(side_effect=lambda mapper=None: mapper([]) if mapper else []),
    ):
        return flask_client.get("/schedule/")


def test_schedule_without_jobs_says_so(flask_client):
    assert b"No scheduled events." in _get_schedule(flask_client, []).data


def test_refresh_secrets_button_runs_the_refresh_under_its_entry(flask_client, unchanged_vault):
    api._ACTIVITY_LOG.clear()
    response = flask_client.get("/api/run/Refresh Secrets")
    assert response.status_code == 200
    (entry,) = api.log_entries()
    assert entry.action == "Running `Refresh Secrets`"
    assert [c.action for c in entry.children] == ["Secrets unchanged", "Tag key for `Alice` read from Find Hub"]


def test_system_page_lists_the_core_system_plugins(flask_client):
    html = flask_client.get("/system/").get_data(as_text=True)
    assert 'data-id="Refresh Secrets"' in html
    assert 'data-id="Enable Notifications" data-name="Enable Notifications" data-duration="0" data-delay="0:00:00" data-no-func' in html
