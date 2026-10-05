# `travel` plugin

Leave-time alerts for a drive or a flight pickup. You enter where you have
to be and when (or a flight number), and orc works backwards from the
arrival time through live traffic, your extras and a buffer, then says
"Time to leave for …" at the right moment.

## Quick start

1. Get a TomTom API key (routing and geocoding) and an AeroDataBox key
   (RapidAPI), and store them in the secrets provider under the names the
   settings give them.
2. Add `plugin Travel orc_extras.travel --section system` to `config.orc`.
3. Create `plugins/orc_extras/travel.orc` (below).
4. Restart orc. The System page's **Travel** button opens the dialog: the
   next three trips, a form for a new one, and a delete per trip.

## Config

```
setting drive_backend        orc_extras.travel.dal.drive.tomtom
setting flight_backend       orc_extras.travel.dal.flight.aerodatabox
setting cron                 '0 6 * * *'
setting window_hours         6
setting tomtom_secret        TOMTOM_KEY
setting aerodatabox_secret   AERODATABOX_KEY
setting http_timeout         120
setting buffer_minutes       10

place Home   '123 Main St, Springfield'
place Office '500 Market St, Metropolis'

extra Coffee   10
extra Parking  20
```

| Setting                | Meaning                                                                             |
| ---------------------- | ----------------------------------------------------------------------------------- |
| `drive_backend`        | `...dal.drive.tomtom` for live routes; `...dal.drive.stub` for a fixed time.        |
| `flight_backend`       | `...dal.flight.aerodatabox` for live flights; `...dal.flight.stub` for a fixed one. |
| `cron`, `window_hours` | Required by the loader; nothing reads them yet.                                     |
| `tomtom_secret`        | Secret name holding the TomTom key.                                                 |
| `aerodatabox_secret`   | Secret name holding the AeroDataBox key.                                            |
| `http_timeout`         | Seconds to wait for either API.                                                     |
| `buffer_minutes`       | Minutes added to every lead time (default 10).                                      |

A `place` is a named address offered as a suggestion in the destination
field; an `extra` is a named chunk of minutes you can tick onto a trip
(parking, a coffee stop). The drive origin is `config.orc`'s `lat`/`long`.

## Behaviour

- A destination is a flight when it looks like one (`AA657`, `dl 1234`);
  anything else is an address or place. A flight's arrival airport,
  terminal and time come from AeroDataBox on the day of arrival; before
  that the trip waits for midnight of that day, and the arrival time you
  typed is replaced by the verified one.
- Lead time is the live drive time to the destination plus the ticked
  extras plus `buffer_minutes`. The trip re-checks at two hours before the
  computed leave time, then every ten minutes, and fires once it is within
  ten minutes of leaving.
- The alert is a `WARNING` spoken as "Time to leave for <place or airport,
  terminal>", or "You're running late for … you'll arrive around HH:MM"
  when the leave time has already passed, logged under the `travel`
  source. Submitting a trip runs the same lookup first, so a bad flight
  number or an address that can't be geocoded is rejected in the dialog.
- Geocoded addresses are cached in the `orc_travel_places` table so a
  repeated destination costs one routing call, not two. Trips persist in
  the job store across restarts.
