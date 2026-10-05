# `lg_tv` plugin

On/off control of an LG webOS TV as an orc device. Off is a webOS command
over the LAN; on is an IR power toggle from a BroadLink blaster, since a TV
that is off doesn't listen on the network. Pairing with the TV happens once,
from the UI.

## Quick start

1. Declare the TV three times in `config.orc`, under the same member name:
   the `LGTV` device orc commands, the `WebOS` device holding the TV's
   hostname, and the `BroadLink` device holding the blaster's IP.
2. Add the pairing button:
   `plugin 'Pair LG TV' orc_extras.lg_tv pair_tv --section device --backend orc_extras.lg_tv.dal.webos`.
3. Restart orc, open the state page's **TV** section and press the TV's
   row, then accept the prompt on the TV screen within its timeout. The
   client key lands in the `orc_lg_tv` table and survives restarts.

## Devices

```
device only LGTV      living living --room Living --name 'Living room TV'
device only WebOS     living lgwebostv.lan
device only BroadLink living 192.168.1.40
```

The `LGTV` member is what routines and react rules name (`LGTV.living off`).
Its `WebOS` namesake is the host the webOS client connects to on port 3000,
and its `BroadLink` namesake is the blaster that sends the toggle. The IR
code comes from the `broadlink_codes` JSON named in `config.orc`, at
`tv.commands.toggle`.

## Behaviour

- `off` sends webOS `power_off` using the stored client key; a TV that is
  already off (port 3000 closed) is left alone. Without a stored key the
  command fails with a log line asking you to pair first.
- `on` sends the IR toggle only when the TV reads as off, so repeating `on`
  can't switch it off again.
- The **TV** state section shows each TV as on or off by probing the port,
  and each row is the pairing button for that TV.
- `--backend orc_extras.lg_tv.dal.stub` fakes the TV for a dev server.
