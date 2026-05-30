import os
import signal
import sys
from datetime import timedelta


def execute_plugin(app, id):
    from orc import config

    getattr(sys.modules[__name__], config.plugins[id])(app)


def reboot(app):
    os.kill(os.getppid(), signal.SIGTERM)


def light_test(app):
    from orc import Light, api, config
    from orc.model import Config

    end = api.local_now() + timedelta(minutes=10)
    app.config_manager.replace_config(Config(Light, config.OFF), end)
    api.light_test()
    app.config_manager.resume(config.default_config)


def back_on_schedule(app):
    from orc import api

    api.replay_day(app.config_manager, api.local_now())


def all_lights_on(app):
    from orc import Light, api
    from orc.model import Config, Configs

    api.execute(Configs(Config(Light, "on"), Config(Light, 100)))


def all_lights_off(app):
    from orc import Light, api
    from orc.model import Config, Configs

    api.execute(
        Configs(
            Config(Light, "off"),
        )
    )


def video_conference(app):
    from orc import Light, api
    from orc.model import Config, Configs

    api.execute(
        Configs(
            Config(Light.OFFICE_TABLE, 5),
            Config(Light.OFFICE_FLOOR, "on"),
            Config(Light.OFFICE_DESK, 50),
        )
    )


def silence(app):
    from orc import Sound, api
    from orc.model import Config, Configs

    api.execute(
        Configs(
            Config(Sound, "stop"),
        )
    )


def sound_test(app):
    from orc import Sound, api
    from orc.model import Config, Configs

    api.execute(Configs(Config(Sound, "https://remote.int.exussum.org/static/alert.mp3")))
