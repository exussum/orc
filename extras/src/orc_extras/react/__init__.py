from functools import partial
from typing import Any, NamedTuple

from command_cfg import each

from orc.loader import Cast, load_plugin_config
from orc.model import AppContext, Devices
from orc_extras.react import plugins

CONFIG = "orc_extras/react"
GRAMMAR = """
react <devices> <state> <action> [--delay=<minutes>]
"""

_TRIGGERS = {"on": "switch", "off": "switch", "open": "contact", "closed": "contact"}


class Rule(NamedTuple):
    devices: Devices
    attribute: str
    state: str
    action: Any
    delay: int | None


def _rule(objects: dict[str, Any], args: Any) -> None:
    attribute = _TRIGGERS.get(args.state)
    if attribute is None:
        raise ValueError(f"Invalid trigger state {args.state!r}: expected one of {sorted(_TRIGGERS)}")
    objects["react"].append(Rule(Cast.devices(args.devices, objects), attribute, args.state, Cast.state(args.action), args.delay))


def declare(declarations: Any) -> None:
    declarations.declare(setup=[setup])


def setup(ctx: AppContext) -> None:
    cfg = load_plugin_config(CONFIG, ctx.config, GRAMMAR, serializers={"react": each(_rule, default=list, types={"delay": int})})
    rules = [(index, rule, {str(d.value): d for d in rule.devices.all()}) for index, rule in enumerate(cfg.react)]
    ctx.api.add_listener(partial(plugins._on_event, ctx, rules))
