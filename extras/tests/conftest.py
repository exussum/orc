# Import orc before the tests import orc_extras: orc's config load imports
# orc_extras.entrance_sensor.plugins, so importing in the other order is circular.
import orc  # noqa: F401
