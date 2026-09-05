# `lg_ac` plugin

Local control for an LG window air conditioner (ThinQ2 "clip" protocol), replacing
LG's cloud. The plugin serves the AC's enrollment over HTTP, runs an embedded MQTT
broker the AC connects to, decodes its binary TLV state, and exposes control.

Calibrated for `WIN_056905_WW` (model `LW1522IVSM`); other models need a one-time
calibration (see below).

## How it talks to the AC

- The AC resolves `hostname` (e.g. `common.lgthinq.com`) via your DNS and hits it
  on **:443** for enrollment: `GET /route`, `GET /route/certificate`,
  `POST /device/<id>/certificate`.
- It then connects to the embedded **MQTT broker on :8883** (TLS, our CA-signed
  server cert) and publishes/subscribes over the clip protocol.
- Server→device traffic goes to `lime/devices/<id>` as JSON `{cmd:"packet",...}`;
  the device publishes state on `clip/message/devices/<id>`.

## Install

```
uv pip install -e 'extras[lg_ac]'        # pulls amqtt (the only extra dep)
```

## Activate

In `src/config.orc`:

```
plugin 'LG AC' orc_extras.lg_ac
```

Settings live in `src/plugins/orc_extras/lg_ac.orc` — set `mqtt_host` to this
machine's LAN IP.

## Certificates (via BWS)

The AC requires a CA-signed server cert with `serverAuth` EKU, chaining to the CA
served at `/route/certificate`. Generate them, then store the four PEMs in
Bitwarden Secrets under the `LG_THINQ_` namespace:

```
python -m orc_extras.lg_ac.gen_certs        # writes certs/lg_ac/{ca,server-ca,...}
```

| BWS secret               | PEM file                    |
| ------------------------ | --------------------------- |
| `LG_THINQ_CA_CERT`       | `certs/lg_ac/ca.crt`        |
| `LG_THINQ_CA_KEY`        | `certs/lg_ac/ca.key`        |
| `LG_THINQ_SERVER_CERT`   | `certs/lg_ac/server-ca.crt` |
| `LG_THINQ_SERVER_KEY`    | `certs/lg_ac/server-ca.key` |

At boot the plugin reads these from BWS and holds them in memory. The broker's TLS
context is built from the in-memory server PEM via a temp file that exists only for
the `load_cert_chain` call, then is deleted — nothing persists to disk. No cert
paths in config.

## DNS + nginx

Point the AC's DNS at this host (e.g. Pi-hole: `common.lgthinq.com → <host>`).

Orc mounts the enrollment blueprint at `/api/lg_ac/enroll/…`, but the AC hits the
domain **root**. nginx terminates TLS on :443 with the LG cert and rewrites:

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

The broker binds `:8883` directly — the AC connects to it without nginx.

## Control

- State shows in orc's dashboard as **Air Conditioner** (state provider).
- Set it: `POST /api/lg_ac/enroll/command` with e.g.
  `{"mode":"cool","temperature":25,"fan_mode":"high"}`. A setpoint frame must
  include `mode`, so send all three fields together.
- Temperatures are Celsius (the device stores °C×2; it displays °F itself).

## A new / different AC model

Field maps live in `fieldmap/<MODEL>.json`, keyed on the `kind` the AC reports at
enrollment. On connect the plugin auto-loads the matching map; an unknown model
logs a warning and runs **capture-only** (state won't decode) until a map exists.

To calibrate a new model:

1. Enable capture: set `capture true` in `lg_ac.orc`, restart. This starts a fresh
   `capture.jsonl` wire log.
2. Run the calibrator while the plugin is up and the AC is enrolled:
   ```
   lg-ac-calibrate
   ```
   It walks you through your modes, fan speeds, and the temperature range (low→high)
   and writes `fieldmap/<MODEL>.json`.
3. Set `capture false` again.

Same model as an existing map → it just works, no calibration.
