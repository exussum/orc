# `calendar` plugin

Turns the next few hours of one or more iCal feeds into spoken alerts: a
short attention tone a couple of minutes before each event and the event's
title read aloud when it starts, with a phone notification that opens the
meeting link. Nothing fires on a day off.

## Quick start

1. Add `plugin Calendar orc_extras.calendar` to `config.orc`.
2. Create `plugins/orc_extras/calendar.orc` with the settings block and one
   `feed` line per calendar (see below).
3. Store each feed's secret iCal URL in the secrets provider under the name
   the `feed` line gives it.
4. Restart orc. The feeds are read on the `cron` schedule, and every event
   in the window becomes two jobs on the schedule page.

## Grammar

```
setting <key> <value>
feed <name> <secret>
```

| Part       | Meaning                                                                                |
| ---------- | -------------------------------------------------------------------------------------- |
| `setting`  | One of the keys below; every key is required.                                          |
| `<name>`   | The feed's name, prefixed to each event id so two feeds can share an event.            |
| `<secret>` | The secret holding the feed's iCal URL; declared as a URL, so a bad one fails startup. |

| Setting           | Meaning                                                                        |
| ----------------- | ------------------------------------------------------------------------------ |
| `backend`         | `orc_extras.calendar.dal.ical` fetches real feeds; `...dal.stub` returns none. |
| `cron`            | When to refresh the feeds (five fields, in `tz`).                              |
| `window_hours`    | How far ahead each refresh looks.                                              |
| `max_events`      | Cap on events taken from one feed per refresh.                                 |
| `warning_minutes` | Lead time for the warning tone before each event.                              |
| `http_timeout`    | Seconds to wait for a feed.                                                    |

## Example

```
setting backend         orc_extras.calendar.dal.ical
setting cron            '10,25,40,55 8-21 * * *'
setting window_hours    20
setting max_events      50
setting warning_minutes 2
setting http_timeout    120

feed default ICS_URL
```

Recurring events are expanded; all-day events are skipped.

## Behaviour

- A refresh replaces the scheduled set: events that left the feed are
  cancelled, new ones are added, and the rest keep their jobs. Refreshes
  are skipped on non-working days, so nothing already scheduled changes
  there either.
- The warning job plays the default alert sound at `ATTENTION` severity.
  The alarm job logs the title under the `calendar` source, speaks it at
  the same severity, and pushes a notification whose click opens the
  event's Google Meet, Zoom or Teams link when one is found in the event.
