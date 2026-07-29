from typing import Any


def declare(declarations: Any) -> None:
    """Called once by orc at config load; delegates to each integration's declare().

    Imports are deferred so importing this package (which happens during orc's own
    config load) doesn't pull the integrations in until registration actually runs.
    """
    from orc_plugins import entrance_sensor, lgtv, yolink

    yolink.declare(declarations)
    lgtv.declare(declarations)
    entrance_sensor.declare(declarations)
