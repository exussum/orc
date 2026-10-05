# `entrance_sensor` plugin

Front-door automation driven by a Hubitat motion sensor. Motion at the door
applies a time-of-day routine and purges presence; when motion clears, a
countdown decides whether someone came home, someone left, or the house is
now empty, and runs the matching routine.

## Quick start

1. Declare the motion sensor and a door contact as `Sensor` devices in
   `config.orc`, and the ad hoc routines the plugin will run.
2. Add `plugin 'Entrance Sensor' orc_extras.entrance_sensor` to `config.orc`.
3. Create `plugins/orc_extras/entrance_sensor.orc` (below).
4. Restart orc. The state page gains an **Entrance Sensors** section with
   both sensors' battery and last activity.

## Grammar

```
setting <key> <value>
message <log> <message>
rules <trigger> <routine>
timed <name> <start> <stop> <routine>
```

| Part      | Meaning                                                                                                                                                          |
| --------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `setting` | One of the keys below; every key is required.                                                                                                                    |
| `message` | The line the cleanup logs for each outcome: `log_present`, `log_door_open`, `log_absent`, `log_shutdown`, `log_nobody`. All five are required.                   |
| `rules`   | The ad hoc routine run for each trigger: `inside`, `present`, `absent`, `shutdown`. All four are required.                                                       |
| `timed`   | A wall-clock window (wrapping midnight when `<start>` is later than `<stop>`) naming an ad hoc or routine id; the first window containing the current time wins. |

| Setting                 | Meaning                                                                                    |
| ----------------------- | ------------------------------------------------------------------------------------------ |
| `cleanup_delay_minutes` | Minutes after motion clears before the cleanup decides; at least 1 when `tag` lines exist. |
| `entrance`              | The motion sensor, as `Sensor.<name>`.                                                     |
| `patio_door`            | A contact sensor; an open door counts as someone being around.                             |
| `active_event`          | The `motion` value that means motion (`active`).                                           |
| `inactive_event`        | The `motion` value that means clear (`inactive`).                                          |
| `snapshot`              | Minutes the `shutdown` scene override lasts before the scene restores itself.              |
| `listener`              | The person whose presence alone means "the visitor left"; must be a `person`.              |

## Example

```
setting cleanup_delay_minutes 2
setting entrance              Sensor.ENTRANCE_SENSOR
setting patio_door            Sensor.PATIO_DOOR
setting active_event          active
setting inactive_event        inactive
setting snapshot              45
setting listener              Rex

message log_present   'Trigger sensor off: skip (people present)'
message log_door_open 'Trigger sensor off: skip (patio door open)'
message log_absent    'Trigger sensor off: skip (listener home)'
message log_shutdown  'Trigger sensor off: applying OFF'
message log_nobody    'Entrance motion with nobody tracked before or after'

rules inside   'All Lights Off'
rules present  Silence
rules absent   Dog
rules shutdown 'All Lights Off'

timed Day   8:00  22:00 'All Lights On'
timed Night 22:00 8:00  Silence
```

## Behaviour

- **Motion active**: presence is paused and purged, any pending cleanup is
  cancelled, and the first `timed` window containing the current time is
  dispatched together with the restore of a previous `shutdown` snapshot.
  Reactions to arrival itself (pause media, light the hall) belong in a
  [react](../react/README.md) rule on the same sensor.
- **Motion inactive**: the `inside` routine runs, and the cleanup is
  scheduled `cleanup_delay_minutes` out.
- **Cleanup**: presence is re-checked with a tag probe and resumed, then
  one branch runs:
  - someone other than the listener is home, or the door is open: `present`;
  - only the listener is home: the pre-visit scene is restored and `absent`
    runs;
  - nobody is home: `shutdown` runs as a scene override for `snapshot`
    minutes, and if nobody was tracked before the visit either, `log_nobody`
    is logged with a phone notification.

Everything logs under the `entrance` source, nested under the walk-in's row.
