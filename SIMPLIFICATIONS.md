# Simplification review — changes from the past 2 weeks

Reviewed 2026-07-25 (commits `ecca808..0cb6d89`, ~60 commits). Four parallel review
passes: presence/networking, view/run routing, device registry/plugins, frontend/CI.
Every finding below was verified against the code at `0cb6d89` before inclusion.
Nothing has been applied.

Related but separate: the log-line truncate fix in `0cb6d89` doesn't show up in
browsers because `runner.py:87` sets `SEND_FILE_MAX_AGE_DEFAULT = 604800` (7-day
static cache) and `base.html` links `/static/tailwind.min.css` with no cache-buster.
Tailwind v4 tree-shakes, so `.truncate` only entered the compiled CSS in that commit;
stale cached CSS lacks it and the div word-wraps. Fix: mtime-based `?v=` query param
on static asset links.

## Worth doing

### 1. ✅ DONE — Drop the dead `force` parameter on `run_iot_job`/`matching_items`
`src/orc/api.py:424,432,437,479-481`

Applied 2026-07-25: removed `force` from both signatures, deleted the
`if force: return rule.items` short-circuit, converted the log branch to plain
if/elif/else, updated the two hardcoded-`False` call sites, deleted
`test_run_iot_job_force_bypasses_presence`, and dropped `force=False` from four
test assertions. 126 tests pass; black/isort clean.

History (answering "when did force used to be used"): the schedule page's
"run now" button. `5aa5e49` (2026-03-17) added `/api/schedule/<id>/run`, which
called `job.func(*job.args, ctx=app.orc, force=True)` → `run_iot_job(force=True)`
to bypass the presence/weather matching when a human forced a scheduled routine.
`a81b09b` (2026-07-20) dropped that endpoint; "run now" now goes through
`/api/run` → `_resolve_run_action`, which dispatches the routine directly
(`api.dispatch(..., force=True)` — dispatch's snapshot-bypass force), skipping
`run_iot_job` entirely. The parameter had no callers for the five days since.

There are THREE distinct `force` params — only one is dead:
- `dispatch(rule, force=True)` (`api.py:168`) bypasses snapshot interception; ~10
  live production callers (snapshot restore/resume, view.py room configs, schedule
  runs, entrance-sensor and chromecast plugins). **Keep.**
- `expire_presence(names, force=True)` (`api.py:322`) → sqlite; live caller at
  `view.py:181`. **Keep.**
- `run_iot_job`/`matching_items` `force` means "bypass the presence check" and
  existed for the old `/force` endpoint, deleted in `a81b09b` (schedule "run now"
  goes through `_resolve_run_action` → `api.dispatch(..., force=True)`, which is
  dispatch's force, not this one). **Dead.**

Grep-verified: `api.py:417` and `api.py:517` hardcode `False`, the scheduler jobs
(`api.py:499`) use the default, and the only `force=True` left is
`tests/test_api.py:322`.

Simplify: remove `force` from `run_iot_job` and `matching_items`; delete
`matching_items`' `if force: return rule.items` short-circuit; `elif not force:` at
432 becomes plain `else:`; line 437 becomes `dispatch(replace(rule, items=matched))`
(same as today — `force` is always False there); delete the
`test_run_iot_job_force_bypasses_presence` test. Pure deletion, no behavior change.

### 2. ✅ DONE — Collapse the six-branch log badge chain
`src/orc/templates/log.html:22-36` (flagged independently by two review passes)

Applied 2026-07-25: replaced the if/elif chain with
`<span class="orc-badge-{{ entry.source.value }}">{{ entry.source.value }}</span>`,
added the `@source inline(...)` safelist to tailwind.src.css, recompiled — all six
`orc-badge-*` classes verified present in the rebuilt tailwind.min.css. Confirmed
`entry.source` is always a `LogSource` enum (in-memory deque, never rehydrated from
sqlite as a plain string), so `.value` is safe including the manual fallback case.

All six branches render `<span class="orc-badge-{source}">{source}</span>`;
`LogSource` values (`model.py:71-77`) are exactly
`calendar/routine/remote/manual/system/plugin` and all six `orc-badge-*` utilities
exist in `src/css/tailwind.src.css:82-103`. So the chain is an identity map:

```jinja
<span class="orc-badge-{{ entry.source.value }}">{{ entry.source.value }}</span>
```

Use `.value` — `str()` of a str-mixin Enum renders `LogSource.CALENDAR` on modern
Python. **Required companion change:** Tailwind v4 only emits `@utility` classes
whose literal names appear in scanned sources; once the literals leave the template a
CSS rebuild purges them. Add one safelist line to `tailwind.src.css`:

```css
@source inline("orc-badge-{calendar,routine,remote,system,plugin,manual}");
```

(supported in the pinned v4.3.1). Net: −12 template lines, +1 CSS line.

### 3. ✅ DONE — Snapshot manager should use its own helpers
`src/orc/api.py` (from `8a8e0d1`, which added `_live` and `get` but didn't reuse them)

Applied 2026-07-25: `resume` now calls `self.get(name)` (which subsumed the
redundant `snapshot and self._live(...)` check — `get` pops and live-checks in one
step; safe because `_lock` is an RLock so the nested `@synchronized` is re-entrant),
and `intercepts` is an if/elif/else over a single
`snapshot = self.snapshots.get(ORC_SYSTEM_SNAPSHOT)` with `not self._live(snapshot)`
replacing the hand-inverted expiry check (`local_now() > end` ≡ `not _live` for a
non-None snapshot). Same branch order and outcomes. 126 tests pass.

- `api.py:255` — `if snapshot and self._live(snapshot):` — `_live` already handles
  None (`bool(snapshot and local_now() <= snapshot.end)`), drop the `snapshot and`.
- `api.py:242-245` vs `251-260` — `resume` reimplements `get` (pop + live-check)
  inline; `get`'s only caller is `tests/test_api.py:47`. Use
  `snapshot = self.get(name)` inside `resume`. Safe: `_lock` is an `RLock`
  (`api.py:223`) so the nested `@synchronized` call is re-entrant.
- `api.py:273-284` — `intercepts` looks up `self.snapshots[ORC_SYSTEM_SNAPSHOT]`
  four times and hand-inverts `_live` (line 277's
  `local_now() > snap.end` is exactly `not self._live(snap)`). Rewrite as guard
  clauses over one `snap = self.snapshots.get(ORC_SYSTEM_SNAPSHOT)`:
  `if snap is None: return False` / SYSTEM-trigger branch / `if not self._live(snap):
  pop and return False` / else log and return True. Same branch order and outcomes.

### 4. ✅ DONE — Merge the parallel dicts in the presence scan
`src/orc/dal/net.py:29-77`

Applied 2026-07-25: `_resolve_targets` now returns one `{ip: (name, mac)}` dict
(plus errors); `_probe_lan` takes a single parameter; the inner `resolve()` returns
`(name, mac, ip_or_exception)` with an `isinstance(res, Exception)` branch, removing
the None sentinels and the `assert`. 126 tests pass (they mock at the
gethostbyname/AsyncSniffer/sendp level, so no test changes needed).

`_resolve_targets` returns `targets: {ip: name}` and `macs: {ip: mac}` with identical
key sets, always built and consumed in lockstep (`net.py:55`, `net.py:77`). One
`{ip: (name, mac)}` dict:

```python
targets[ip] = (name, mac)
probes = [Ether(dst=mac) / ARP(pdst=ip, hwdst=mac) for ip, (_, mac) in targets.items()]
return {name for ip, (name, _) in targets.items() if ip in responded}
```

`_probe_lan(targets)` drops to one parameter. Safe: `scan_presence` is the only
caller of both helpers; tests mock at the `socket.gethostbyname`/`AsyncSniffer`/
`sendp` level (`tests/test_api.py:361-382`), not the helper boundary.

Related, same function: the inner `resolve()` (`net.py:22-27`) returns
`(name, mac, ip | None, exc | None)`, forcing an `assert ip is not None` at line 37.
Return `(name, mac, ip_or_exception)` and branch on `isinstance(res, Exception)` —
removes the assert and a tuple slot.

### 5. ✅ DONE — Replace hand-rolled conftest save/restore with `monkeypatch`
`tests/conftest.py:33-48` (from `6e80b54`)

Applied 2026-07-25: the `_core_registry` fixture now takes `monkeypatch` and uses
`setattr(..., raising=False)` for the four `orc` attributes and `config.registry`;
`saved_attrs`, `saved_registry`, and the try/finally are gone. The save/restore
PURPOSE is unchanged — core tests still get the lightweight test enums and the
plugin suite still sees the real registry afterward — only the mechanism moved to
pytest's built-in undo (which also correctly deletes, rather than None-ing, an
attribute that didn't exist before). Verified both suites pass as the pre-commit
hook runs them (126 core + 26 plugin).

The fixture manually builds `saved_attrs` via `getattr(orc, name, None)`, saves
`orc.config.registry`, and restores in try/finally. `monkeypatch.setattr(...,
raising=False)` does all of it and deletes ~8 lines. It's also more correct: the
current code restores a previously-missing attribute as `None`; monkeypatch deletes
it on undo. Preserves exactly what `6e80b54` was for (plugin suites see the real
registry afterwards).

### 6. ⏭ SKIPPED (2026-07-25, user decision) — One registration path in the device registry
`src/orc/device_registry.py:36-41` + `src/orc/api.py:159-164`

`register_dispatch` is called only by `api.register_core`; `register_device_type` is
called only from inside `register_plugin` itself (line 66). Make `register_core`
call `core.register_plugin(dispatch={"Light": _dispatch_light, "Chromecast":
_dispatch_chromecast})` and delete both methods (inline the dedup:
`if name not in self.device_types: self.device_types.append(name)`). Grep-verified
no other callers across src/, plugins/, tests/ (conftest goes through
`register_core`). Leaves exactly one registration path for core and plugins.

### 7. Finish what two commits started

- **7a. ❌ NOT A FINDING (2026-07-25, user: intentional)** —
  `src/orc/view.py:160-167` — `record_duration` wrapping the whole `if delay:`
  block looked like it undid `41ba1b6` ("Stop timing background tasks"), since
  queuing a delayed job records the ~microsecond `scheduler.add_job` call into the
  id's rolling average while the delayed execution stays untimed. The user confirmed
  the current wrapping is intentional — do not re-flag or "fix" this.
- **7b. ✅ DONE (2026-07-25)** — `.pre-commit-config.yaml:58-63` — `e959de3`
  ("Stop auditing if the lock file hasn't changed") gated `pylock` but left
  `pip-audit` on `always_run: true`. Changed to `files: ^pylock\.toml$` —
  `pip-audit --locked .` reads pylock.toml, so it now skips exactly when pylock
  skips. CI still audits every build (`pre-commit run --all-files` passes every
  tracked file, and pylock.toml is tracked).

## Smaller cleanups

- **Unreachable zero-workers guard + dead DB read** — `src/orc/dal/net.py:32` /
  `src/orc/api.py:350-352`. The `max(1, len(pairs))` guard from `49dcb1c` never
  fires: the only caller (`check_presence`) returns early on empty `pairs`. If
  dal-boundary defensiveness is wanted, an explicit `if not pairs: return set(), []`
  at the top of `scan_presence` reads as intent. (Keep `_probe_lan`'s
  `if not targets` guard at `net.py:48` — reachable when all DNS resolution fails.)
  Also `api.py:352`'s `before = present_names()` feeds only the `if not silent:`
  logging, but `plugins.py:33` calls `check_presence(silent=True)` every cycle —
  a dead DB read each time. `before = present_names() if not silent else set()`
  (must stay before `mark_present`).
- **Unlinked sniff-window constants** — `src/orc/dal/net.py:62,67-69`. `timeout=3`
  and `range(3)`/`sleep(1)` describe the same 3-second window; changing one without
  the other silently drops probes outside the sniff window. One `_WINDOW_S = 3`
  module constant for both.
- **Move the run-group shape into the view** — `src/orc/view.py:219-222` +
  `log.html:12-20,38`. The view ships raw `list(run)` groups; the template does
  `run|first` / `run|last` / `run|length` in three places (entries are newest-first
  via `appendleft`, `model.py:102`, so `first` = end time, `last` = start). Shape
  `(entry, count, start, end)` in the view instead; `entries_grouped` is consumed
  only by log.html (grep-verified). Also in log.html: lines 15-20 duplicate the
  timestamp span — emit only the range prefix conditionally; lines 44-52 wrap
  "No activity yet." in a full one-cell table — a plain card div renders the same.
- **Delete the Date gymnastics in scene.js** — `src/orc/static/scene.js:12-28`.
  `view.py:203` emits zero-padded `"%H:%M"`, so lexicographic comparison is exact:
  `const now = new Date().toTimeString().slice(0, 8)` and plain
  `start <= now && now <= end`. Identical boundary semantics (inclusive at start,
  exclusive past the end minute's :00). Deletes the `new Date("01/01/00 " + start)`
  preprocessing block entirely.
- **Collapse get-then-reload copies** — `src/orc/static/presence.js` has three
  (`checkin`/`expire` identical except the path segment; `runCheck` differs only in
  URL); `scene.js:8-10` (`.orc-pause`) is the same shape. One
  `async function getReload(url, el) { if (await get(url, el)) location.reload(); }`
  in orc.js, shared (orc.js loads first via base.html, all with `defer`).
- **`onClick` helper** — `querySelectorAll(...).forEach(addEventListener("click"))`
  appears 7×: `scene.js:1`, `device.js:75,81,99`, `schedule.js:38`,
  `presence.js:13,17`, plus `system.html:114`. A 3-line
  `onClick(sel, fn)` in orc.js makes each a one-liner. Note: a single fully-delegated
  `.orc-runner` listener is NOT a win — the three pages attach genuinely different
  behavior (confirm+dropup-close / selectRunner / plain run).
- **Duplicated after-hours confirm** — `src/orc/static/orc.js:92-101`.
  `run_with_confirm` and `run_config` open with the identical
  after-hours-confirm line. Extract
  `confirmAfterHours(el)` returning `hours >= 9 || confirm(...)`.
- **yolink `captured` dict workaround** — `plugins/src/orc_plugins/yolink/dal.py:216-238`.
  `_on_message` carries `{"name": ..., "transitions": [...]}` but `current.name` is
  available where each transition is appended. Use a flat
  `(name, kind, old, new)` list like `_set_connected` (lines 243-254) already does —
  makes the two callback-collection sites symmetrical. (`captured["name"]` is set iff
  transitions were appended, so the forms are equivalent.)
- **Registry build copies + lookup** — `src/orc/device_registry.py:85-91`: the
  `dict(...)`/`list(...)` defensive copies in `build()` protect a builder both call
  sites (`src/orc/__init__.py:59`, `tests/conftest.py:42`) immediately discard.
  `device_registry.py:109-112`: module/register lookup collapses to
  `if register := getattr(sys.modules.get(package), "register", None): register(builder)`
  (missing module and missing attribute both yield None).
- **Dual pointerdown+click on AC buttons** — `src/orc/static/device.js:74-79`. The
  `pointerdown → selectOne` listener exists only to paint the highlight a few ms
  before `click`. Call `selectOne` at the top of the click handler and drop it. Only
  behavior change: press-then-drag-away no longer moves the highlight (a correction).
- **pylock trigger includes pyproject.toml unnecessarily** —
  `.pre-commit-config.yaml:51-56`. `uv export --frozen` reads only uv.lock; a
  pyproject change that matters rewrites uv.lock via the uv-lock hook, which then
  triggers pylock. `files: ^(uv\.lock|pylock\.toml)$` avoids no-op exports on
  doc/config edits.

## Considered and left alone (with reasons)

- **Entrance-sensor snapshot/replay flow**
  (`plugins/src/orc_plugins/entrance_sensor/plugins.py:61-83`) — the three off-job
  branches genuinely differ in snapshot action, message, and dispatch rules; the
  snapshot doubling as the "empty-house visit in progress" flag is the compact
  encoding. An explicit state enum would be more code. Lines 76/81 dispatch
  `sensor.rules.absent` identically and could merge, but at the cost of the flat
  if/elif/else with parallel `msg =` assignments — a wash. Line 70's
  `snapshot_manager.get(SNAPSHOT_NAME)` called for its pop side effect is carried by
  the comment; a `discard()` method would mean extending core for a plugin need,
  which this repo avoids.
- **`_resolve_targets`/`_probe_lan` split** — two ~30-line phases with distinct
  concerns; merging makes one ~60-line function.
- **ARP-unicast + mDNS + 3× resend layering** in net.py — each layer has a
  documented power-save-phone rationale in comments; `90e5c2e`'s manual loop over
  `sendp(inter=...)` is a deliberate fix for runtime scaling with device count.
- **`_check_presence_job` wrapper** (`api.py:530-532`) — required by the
  `ContextThreadPoolExecutor` ctx-injection pattern shared with the other cron jobs
  (`api.py:443-446`); `check_presence` shouldn't take a `ctx` it doesn't use.
- **`silent` parameter on `check_presence`** — live caller at `plugins.py:33`.
- **`PluginCtx` module fields** (`ctx.api`/`ctx.model`/`ctx.orc`) — documented
  plugin API surface (`plugins/README.md:54-61`), exists for the config-load import
  cycle.
- **Core-defaults-in-builder vs `register_core` split**
  (`device_registry.py:26-27` vs `api.py:159`) — load-bearing: the bootstrap
  `Config()` load at `src/orc/__init__.py:50` runs before `orc.api` is importable
  but still needs `device_types` to build enums.
- **`selectRunner` → CSS radio/`:has()`** (`device.js:93-104` +
  `device.html:52-57`) — legitimate but roughly line-neutral; the win would be
  deleting stateful JS, not lines. Optional.
- **CI vs pre-commit** — no duplicated work found: setup-python 3.14 feeds
  `default_language_version`, portaudio19-dev feeds pytest's native deps,
  `lfs: true` feeds the tailwind-recompile diff check against LFS-tracked
  `tailwind.min.css`, and the tool installs are `language: system` hook
  prerequisites.
