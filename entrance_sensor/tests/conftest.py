# Import orc before the tests import orc_entrance_sensor: orc's config load imports
# orc_entrance_sensor.plugins, so importing in the other order is circular.
import orc  # noqa: F401
