import os
import sys
from functools import wraps


def requires_enabled(stub):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not os.getenv("ORC_ENABLED"):
                print(f"[disabled] {fn.__name__} args={args} kwargs={kwargs}", file=sys.stderr)
                return stub(*args, **kwargs) if callable(stub) else stub
            return fn(*args, **kwargs)

        return wrapper

    return deco


from orc.dal.bws import fetch_secrets  # noqa: E402, F401
from orc.dal.chromecast import (  # noqa: E402, F401
    fetch_config,
    fetch_state,
    fetch_youtube_stream_metadata,
    pause,
    play,
    resume,
    set_volume,
    stop,
)
from orc.dal.discovery import fetch_hubitat_config, ping_host  # noqa: E402, F401
from orc.dal.feeds import fetch_holidays, fetch_ical  # noqa: E402, F401
from orc.dal.lights import fetch_light_state, update_light  # noqa: E402, F401
from orc.dal.sqlite import (  # noqa: E402, F401
    delete_all_presence,
    delete_presence,
    delete_theme_override,
    fetch_presence,
    fetch_theme_override,
    init_db,
    insert_presence,
    insert_theme_override,
)
