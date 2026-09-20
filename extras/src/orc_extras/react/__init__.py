from datetime import timedelta
from functools import partial
from typing import Any

from command_cfg import each

from orc.kernel import engine
from orc.kernel.loader import Cast, load_plugin_config, validate_ac_state
from orc.model import AcCommand, AcState, AppContext, DeviceEnum, Devices, MqttDeviceChannel, Playback
from orc_extras.react import plugins
from orc_extras.react.plugins import TRIGGERS, When

CONFIG = "orc_extras/react"
GRAMMAR = """
react <devices> turns <state> set <action> [if <device> is <condition>] [--delay=<minutes>]
react <devices> turns <state> set <target> <action> [if <device> is <condition>] [--delay=<minutes>]
"""


_AC_CONDITIONS = {name.lower(): state for name, state in AcState.__members__.items()}
_CHROMECAST_CONDITIONS = {state.value: state for state in Playback}


def _is_type(device: DeviceEnum, objects: dict[str, Any], type_name: str) -> bool:
    cls = objects["device"].enums.get(type_name)
    return bool(cls and isinstance(device, cls))


def _parse_target(target: str | None, action: Any, objects: dict[str, Any]) -> Devices | None:
    if target is None:
        if not isinstance(action, AcCommand):
            return None
        elif (ac_cls := objects["device"].enums.get("AC")) is None:  # a defined-but-empty AC enum is falsy yet still a valid target
            raise ValueError(f"AC command {action} requires an AC device type")
        return Devices(ac_cls)
    devices = Cast.devices(target, objects)
    validate_ac_state(devices.all(), action, objects["device"].enums, source=target)
    return devices


def _parse_when(device: DeviceEnum, condition: str, objects: dict[str, Any]) -> When:
    if _is_type(device, objects, "AC"):
        state = _AC_CONDITIONS.get(condition)
        if state is None:
            raise ValueError(f"Invalid if condition {condition!r}: expected one of {sorted(_AC_CONDITIONS)}")
        return When(device, state)
    elif _is_type(device, objects, "Chromecast"):
        playback = _CHROMECAST_CONDITIONS.get(condition)
        if playback is None:
            raise ValueError(f"Invalid if condition {condition!r}: expected one of {sorted(_CHROMECAST_CONDITIONS)}")
        return When(device, playback)
    elif condition not in TRIGGERS:
        raise ValueError(f"Invalid if condition {condition!r}: expected one of {sorted(TRIGGERS)}")
    return When(device, condition)


def _rule(objects: dict[str, Any], args: Any) -> None:
    attribute = TRIGGERS.get(args.state)
    if attribute is None:
        raise ValueError(f"Invalid trigger state {args.state!r}: expected one of {sorted(TRIGGERS)}")
    action = Cast.state(args.action)
    target = _parse_target(args.target, action, objects)
    when = _parse_when(Cast.device(args.device, objects), args.condition, objects) if args.device else None
    cond = plugins.condition(when)
    delay = timedelta(minutes=args.delay) if args.delay else timedelta()
    for source in Cast.devices(args.devices, objects).all():
        command = engine.Command(target or Devices(source), action)
        trigger = engine.Transition(MqttDeviceChannel(source, attribute), args.state)
        objects["react"].append(engine.Rule(trigger, (engine.Clause(cond, command),), delay, cooldown=plugins.COOLDOWN))


def declare(declarations: Any) -> None:
    declarations.declare(setup=[setup])


def setup(ctx: AppContext) -> None:
    cfg = load_plugin_config(CONFIG, ctx.config, GRAMMAR, serializers={"react": each(_rule, default=list, types={"delay": int})})
    engine_rules = cfg.react
    ctx.engine.add_rules(engine_rules)
    sources = {plugins.source_of(er).value: plugins.source_of(er) for er in engine_rules}
    ctx.plugin_state[plugins] = plugins.React({hash(er): er for er in engine_rules}, sources)
    ctx.api.add_listener(partial(plugins._on_event, ctx))
