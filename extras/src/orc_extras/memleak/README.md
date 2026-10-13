# memleak

Tracks where the process's memory grows, hour by hour.

## Setup

1. Add `plugin 'Memory Leak' orc_extras.memleak --section system` to `config.orc`.
2. Copy `examples/configs/memleak.orc` to `plugins/orc_extras/memleak.orc` beside it.
3. Open the System page and press **Memory Leak**.

## Settings

| Setting           | Meaning                                              |
|-------------------|------------------------------------------------------|
| `threshold_kib`   | Rows that grew less than this since boot are dropped |
| `snapshot_minute` | Minute of every hour the snapshot runs               |

## What it does

- Turns on `tracemalloc` at declare time and takes a snapshot at boot and once an hour, keeping the last 48.
- Groups allocations by file and line, folding the import machinery (`importlib`, `abc`, `enum`, `dataclasses`, `re`) into one row.
- Ranks rows by how many hourly intervals they grew in, then by KiB per hour, and drops rows under the threshold since boot. A one-time import grows in one interval; a leak grows in most of them.

## Columns

| Column     | Meaning                                           |
|------------|---------------------------------------------------|
| Location   | File and line of the allocation                   |
| KiB        | Current size                                      |
| KiB/h      | Growth since boot divided by hours since boot     |
| Grown      | Intervals the row grew in, out of intervals kept  |
| Since boot | Growth since the first snapshot                   |

The raw rows are at `/api/memleak/memory/`.
