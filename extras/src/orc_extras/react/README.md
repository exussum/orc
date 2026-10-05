# `react` plugin

Event-driven rules: when a Hubitat device reports a change, a `react` rule
can set other devices, after an optional delay and under an optional
condition. Rules live in `$ORC_CONFIG_DIR/plugins/orc_extras/react.orc` and
load once at startup.

## Quick start

1. Add `plugin React orc_extras.react --section system` to `config.orc`.
2. Create `plugins/orc_extras/react.orc` with one rule per line (see below).
3. Restart orc. The System page's **React** button lists every rule name
   with a switch that puts it to sleep for its `--pause`.

## Grammar

```
react <name> <devices> turns <to> [if <condition>...] set [<target>] <action> [--delay=<minutes>] [--pause=<minutes>]
react <name> <devices> if <condition>...            set [<target>] <action> [--delay=<minutes>] [--pause=<minutes>]
```

| Part          | Meaning                                                                                                                                        |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `<name>`      | Groups rules for the System page's sleep switch; quote names with spaces.                                                                      |
| `<devices>`   | The Hubitat devices to watch — one, a type, or a set: `Light.desk`, `Light`, `'{Light.a, Light.b}'`. Each device becomes its own rule.        |
| `turns <to>`  | Fire when the device's attribute becomes `<to>`: `on`/`off` (switch), `open`/`closed` (contact), `active`/`inactive` (motion).                 |
| `if …`        | A condition that must hold when the rule is evaluated, written as one quoted expression (vocabulary below). Without `turns`, it is the trigger. |
| `<target>`    | The devices to set; defaults to the watched device, or to the AC for an AC action.                                                             |
| `<action>`    | `on`, `off`, a Chromecast state (`pause`), or an AC command `mode:fan:temp` such as `cool:low:72`.                                             |
| `--delay`     | Minutes to wait before setting. A `turns` rule drops a pending command if the attribute changes again first.                                   |
| `--pause`     | Minutes the System page's sleep switch silences the rule (default 10).                                                                         |

A rule without `turns` is evaluated on every report from its device and the
`if` decides; write a measurement condition as `Entered(...)` so it fires once
when the value crosses into the range rather than on every reading.

## The `if` expression

The expression is Python, evaluated at startup over this vocabulary and
nothing else (no builtins). Quote it once on the config line; state words
inside it are strings, so use the other quote: `if 'Eq(switch, "on")'`.

| Expression                     | Holds when                                                                            |
| ------------------------------ | ------------------------------------------------------------------------------------- |
| `And(a, b, …)` / `Or(a, b, …)` | All / any of the conditions hold.                                                     |
| `Eq(<subject>, "<state>")`     | The subject reads that value.                                                         |
| `Has(<subject>)`               | The subject reads anything but `None`.                                                |
| `Became(<attribute>, "<to>")`  | The attribute just changed to that state (what `turns` uses).                         |
| `Range(<measure>, low, high)`  | The measurement is inside `low..high` (level: holds on every reading in range).       |
| `Entered(<measure>, low, high)`| The measurement just crossed into `low..high` (edge: holds on that reading only).     |
| `AcIs(AC.<member>, "<state>")` | The AC is in that state: `on`, `off`, `cool`, `dry`, `fan_only`, `econ`.              |
| `Playing(Chromecast.<member>)` | That Chromecast is playing.                                                           |
| `Present("alice", "bob")`      | Any of the named people is home.                                                      |
| `Anyone()` / `Nobody()`        | Someone / no one is home.                                                             |

Names resolve in two layers:

- A bare name is an attribute of the rule's own device: `switch`,
  `temperature`, `humidity`.
- A dotted name is absolute: `Light.desk.switch` is that light's attribute;
  `Chromecast.tv` and `AC.living` are the whole device.
- `dewpoint(temperature,humidity)` is a measurement computed from the
  device's attributes (°F); it can stand wherever an attribute can.

## Examples

```
react 'Lights off' Light turns on set off --delay=15 --pause=30
react 'Cool'       Sensor.LIVING_ROOM if 'Entered(temperature, 68, 75)' set AC cool:low:72
react 'Dry'        Sensor.LIVING_ROOM if 'And(Present("alice","bob"), Entered(dewpoint(temperature,humidity), 50, 60))' set AC fan_only:low:70
react 'Dry off'    Sensor.LIVING_ROOM if 'And(AcIs(AC.LIVING_ROOM, "on"), Entered(dewpoint(temperature,humidity), 0, 55))' set AC off
react Arrive Sensor.ENTRANCE_SENSOR turns active set Light      on
react Arrive Sensor.ENTRANCE_SENSOR turns active set Chromecast pause
```

A long expression can continue on indented lines:

```
react 'Cool the house when it gets warm'
      '{Sensor.OFFICE, Sensor.BEDROOM}'
      if 'And(
             Anyone(),
             Eq(Sensor.PATIO_DOOR.contact, "closed"),
             Entered(dewpoint(temperature,humidity), 60, 100)
          )'
      set AC cool:low:77
      --pause=180
```

## Behaviour

- Each fired rule logs `` `<device>` <to or name> → set <targets> <action> ``
  under the `react` source; a delayed rule logs when it runs.
- A rule won't fire twice within 10 seconds for the same device.
- Two rules firing on one report are merged into a single dispatch; if they
  disagree about a device, the log says so.
