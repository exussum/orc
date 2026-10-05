# `yolink` plugin

YoLink leak sensors as `Leak` devices. A wet sensor is an emergency: the
message is spoken and broadcast at `EMERGENCY` severity at any hour and
pushed to every phone. Everything else about the sensors (dry again, low
battery, weak signal, going offline) is an `ATTENTION` alert.

## Quick start

1. In the YoLink app, create a User Access Credential and store its id and
   secret in the secrets provider as `YOLINK_ID` and `YOLINK_SECRET`.
2. Declare each sensor in `config.orc` with its YoLink device id as the
   target.
3. Add the plugin line:
   `plugin 'Test Leak Sensor' orc_extras.yolink test_sensor --section device --backend orc_extras.yolink.dal.yosmart`.
4. Restart orc. The state page gains a **Leak Sensors** section with each
   sensor's state, battery, signal, report interval and last change.

## Devices

```
device define Leak
device add Leak KITCHEN d88b4c0100012345 --name 'Kitchen sink'
device add Leak LAUNDRY d88b4c0100016789 --name 'Washing machine'
device seal Leak
```

The label is the name used in every log line and notification. With no
`Leak` devices the plugin logs that it is skipping and does nothing.

## Behaviour

- At boot a background thread authenticates against the YoLink cloud,
  fetches every sensor's current state, and subscribes to its MQTT feed;
  it re-authenticates before the token expires. If that thread ever dies,
  orc exits so the process manager restarts it.
- Each change is logged once, under the `plugin` source, with the
  sensor's name: water detected or cleared, battery dropping to or
  recovering from critical, signal crossing -90 dBm, report interval
  changes, online/offline, and the cloud connection dropping or returning.
  A drop counts only after three disconnects within a minute, since the
  client reconnects transient ones itself.
- Notifications carry the tag `leak:<name>` for water and `yolink:<name>`
  for the rest, so a phone shows one current line per sensor and the dry
  line replaces the wet one.
- Pressing a sensor's row in **Leak Sensors** runs the full wet path for
  that sensor and clears it five seconds later: a drill for the alert
  devices. `--backend orc_extras.yolink.dal.stub` leaves the cloud out for
  a dev server.
