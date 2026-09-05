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

Settings live in `src/plugins/orc_extras/lg_ac.orc` — set `fqdn` to this server's
real FQDN (it must resolve to the LAN IP the AC reaches; that IP is what the device
uses for MQTT and is baked into the server cert's SAN). `fqdn` ships as the
placeholder `lg-ac.example`; both the plugin (at startup) and `gen_certs` abort
while it still ends in `.example`.

## Certificates (via BWS)

The AC requires a CA-signed server cert with `serverAuth` EKU, chaining to the CA
served at `/route/certificate`. Store four PEMs in Bitwarden Secrets under the
`LG_THINQ_` namespace — each secret's **value is the PEM text itself**:

| BWS secret             | value                          |
| ---------------------- | ------------------------------ |
| `LG_THINQ_CA_CERT`     | CA certificate (PEM)           |
| `LG_THINQ_CA_KEY`      | CA private key (PEM)           |
| `LG_THINQ_SERVER_CERT` | server certificate, CA-signed (PEM) |
| `LG_THINQ_SERVER_KEY`  | server private key (PEM)       |

`gen_certs` produces them locally; paste each file's contents into the matching
secret:

```
python -m orc_extras.lg_ac.gen_certs
# ca.crt → LG_THINQ_CA_CERT, ca.key → LG_THINQ_CA_KEY,
# server-ca.crt → LG_THINQ_SERVER_CERT, server-ca.key → LG_THINQ_SERVER_KEY
```

At boot the plugin reads these PEMs from BWS and holds them in memory. The broker's
TLS context is built from the in-memory server PEM via a temp file that exists only
for the `load_cert_chain` call, then is deleted — nothing persists to disk. No cert
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

1. Enable capture: set `capture True` in `lg_ac.orc`, restart. This starts a fresh
   `capture.jsonl` wire log.
2. Run the calibrator while the plugin is up and the AC is enrolled:
   ```
   lg-ac-calibrate
   ```
   It walks you through your modes, fan speeds, and the temperature range (low→high)
   and writes `fieldmap/<MODEL>.json`.
3. Set `capture False` again.

Same model as an existing map → it just works, no calibration.
