# `lg_ac` plugin

Local control of an LG window air conditioner, with no LG cloud. The AC is
pointed at orc by DNS, enrols against it over HTTPS, and then holds an MQTT
session to a broker embedded in the plugin. orc decodes the AC's binary
state and exposes it as the built-in `AC` device: the device page's
mode/fan/temperature card, routines and react rules all work on it.

Calibrated for `WIN_056905_WW` (model `LW1522IVSM`); another model needs a
one-time calibration (last section).

## Credit

The ThinQ2 "clip" protocol (enrollment, the AABB/TLV framing, the
`lime/devices` transport and the field maps) was reverse-engineered by
**anszom** in the **rethink** project — https://github.com/anszom/rethink.
This plugin is a Python reimplementation of that work for orc.

## Quick start

1. Install with the extra: `pip install './extras[lg_ac]'` pulls `amqtt`,
   the embedded broker.
2. Add `plugin 'LG AC' orc_extras.lg_ac` to `config.orc` and create
   `plugins/orc_extras/lg_ac.orc` (below) with `fqdn` set to this server's
   real name.
3. Generate the certificates and store the four PEMs as secrets
   ([Certificates](#certificates)).
4. Point the AC's DNS at this host and put the nginx `:443` server block in
   front of the enrollment routes ([DNS + nginx](#dns--nginx)).
5. Start orc and power-cycle the AC: it enrols itself. Read its clip id
   with `curl -sk https://<host>/api/lg_ac/enroll/devices`.
6. Declare the device with that id as its target
   ([The AC device](#the-ac-device)) and restart.

## Config

```
setting hostname          common.lgthinq.com
setting fqdn              lg-ac.example
setting https_advertise   443
setting mqtt_port         1883
setting mqtts_advertise   8883
setting capture           False
```

| Setting           | Meaning                                                                                           |
| ----------------- | ------------------------------------------------------------------------------------------------- |
| `hostname`        | The LG name the AC resolves; your DNS sends it here.                                              |
| `fqdn`            | This server's real FQDN. Its LAN IP is what the AC connects to for MQTT and is in the cert's SAN. |
| `https_advertise` | The HTTPS port told to the AC at enrollment (nginx listens there).                                |
| `mqtt_port`       | The broker's plain port, loopback only; orc and `lg-ac-calibrate` talk to it.                     |
| `mqtts_advertise` | The TLS port the broker binds and the AC is told to connect to.                                   |
| `capture`         | Buffer recent wire frames in memory for `/api/lg_ac/enroll/capture`; only for calibration.        |

Startup refuses an `fqdn` still ending in `.example`, and so does `gen_certs`.

## Certificates

The AC requires a CA-signed server cert with the `serverAuth` EKU, chaining
to the CA it fetches at `/route/certificate`. Generate them once:

```
python -m orc_extras.lg_ac.gen_certs
```

and store each file's text as a secret. Startup checks all four as PEM
before any plugin runs.

| Secret                 | File            | Content                    |
| ---------------------- | --------------- | -------------------------- |
| `LG_THINQ_CA_CERT`     | `ca.crt`        | CA certificate             |
| `LG_THINQ_CA_KEY`      | `ca.key`        | CA private key             |
| `LG_THINQ_SERVER_CERT` | `server-ca.crt` | server certificate, signed |
| `LG_THINQ_SERVER_KEY`  | `server-ca.key` | server private key         |

The PEMs are held in memory; the broker's TLS context is built through a
temp file that exists only for the `load_cert_chain` call. Nothing cert-
related is written to disk and there are no cert paths in config.

## DNS + nginx

Point `hostname` at this host in the DNS the AC uses (Pi-hole, the router).

The enrollment routes are mounted at `/api/lg_ac/enroll/…`, but the AC
calls the domain root, so nginx terminates TLS on `:443` with the LG server
cert and rewrites the three paths:

```nginx
server {
    listen 443 ssl;
    server_name common.lgthinq.com;
    ssl_certificate     certs/lg_ac/server-ca.crt;
    ssl_certificate_key certs/lg_ac/server-ca.key;
    location = /route                     { proxy_pass http://127.0.0.1:8000/api/lg_ac/enroll/route; }
    location = /route/certificate         { proxy_pass http://127.0.0.1:8000/api/lg_ac/enroll/route/certificate$is_args$args; }
    location ~ ^/device/(.+)/certificate$ { proxy_pass http://127.0.0.1:8000/api/lg_ac/enroll/device/$1/certificate; }
}
```

The broker binds `mqtts_advertise` directly; the AC reaches it without
nginx.

## The AC device

Declare each unit as an `AC` with its clip id as the target. One unit:

```
device only AC main 6c9aff96-6337-17b6-82f7-2887613a8910 --sort 2
```

Several:

```
device define AC --sort 2
device add AC living  6c9aff96-…-A --room 'Living Room' --name 'Living Room AC'
device add AC bedroom 6c9aff96-…-B --room Bedroom       --name 'Bedroom AC'
device seal AC
```

Each unit is its own card on the device page and its own row in the state
page's **AC** section. A unit whose target isn't an enrolled clip id falls
back to the single connected device.

## Control

- The device card's mode, fan and temperature controls, `routine` lines and
  react actions (`cool:low:72`) all reach the AC through this plugin;
  temperatures are Fahrenheit everywhere in orc and converted to the half
  degrees Celsius the AC stores.
- Direct: `POST /api/lg_ac/enroll/command` with
  `{"mode":"cool","temperature":77,"fan_mode":"high"}` (a setpoint frame
  must carry `mode`, so send all three), and `GET /api/lg_ac/enroll/state`
  for the decoded state; add `device=<clip id>` with several units.
- Every state report the AC sends is logged in command vocabulary, so it
  nests under the rule or button that asked for it.

## A new or different AC model

Field maps live in `fieldmap/<MODEL>.json`, keyed on the model the AC
reports at enrollment. On connect the matching map loads; an unknown model
logs a warning and runs capture-only (state won't decode) until a map exists.

1. Set `capture True` in `lg_ac.orc` and restart. Recent frames are at
   `GET /api/lg_ac/enroll/capture`.
2. With the plugin up and the AC enrolled, run `lg-ac-calibrate`. It walks
   through your modes, fan speeds and temperature range and writes
   `fieldmap/<MODEL>.json`.
3. Set `capture False` again.

Or write the file by hand:

```json
{
  "fields":      { "power": "0x1f7", "mode": "0x1f9", "fan_mode": "0x1fa",
                   "current_temperature": "0x1fd", "temperature": "0x1fe" },
  "modes":       { "cool": 0, "fan_only": 2, "econ": 8, "dry": 1 },
  "fans":        { "low": 2, "mid": 4, "high": 6 },
  "temperature": { "divisor": 2, "min": 16, "max": 30 }
}
```

- `fields`: logical field to TLV id (hex). Consistent across the clip
  family, so usually copied verbatim.
- `modes` / `fans`: name to the raw code. Model-specific: flip each mode and
  fan on the unit and read the value off `/capture`.
- `temperature`: `raw = °C × divisor`; `min` and `max` in °C.
