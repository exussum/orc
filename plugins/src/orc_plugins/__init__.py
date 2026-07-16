from typing import Any


def register(core: Any) -> None:
    """Called once by orc at config load; delegates to each integration's register().

    Imports are deferred so importing this package (which happens during orc's own
    config load) doesn't pull the integrations in until registration actually runs.
    """
    from orc_plugins import lgtv, yolink

    yolink.register(core)
    lgtv.register(core)
