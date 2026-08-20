# `example` plugin

Reference plugin exercising every `orc_extras` pattern. Logic bodies are stubs
(`pass`, or `raise NotImplementedError` where a value must be returned); copy it,
fill them in, and delete what you don't need.

```
example/
  __init__.py            declare() (all kinds) + setup() (load config, resolve backends, init db, set runtime)
  model.py               shared types: Settings, Widget/Zone, Runtime, ExampleJob
  plugins.py             core logic
  web.py                 Flask blueprint (/api/example/things/)
  static/example.js      scene-button browser hook
  dal/
    interfaces.py        FooService / BarService Protocols
    sqlite.py            Connection + table access (injected connection)
    foo/  acme.py stub.py    capability: provider + stub
    bar/  globex.py stub.py
```

## Activation

A plugin runs only when the **main** config (`src/config.orc`) names it:

```
plugin Example orc_extras.example
```

Its own `.orc` (under `src/plugins/**`) is separate — read into
`config.plugin_configs`, consumed lazily by `load_plugin_config` in `setup()`.
No `plugin` line ⇒ the module is never imported (why `example` is inert).

## Where things go

**Config/domain types** — inline in `__init__.py` if only `setup()` uses them
(`calendar`, `entrance_sensor`); a leaf `model.py` once a second module needs
them (`travel`, `example`). Never leave a shared type in `__init__.py` (importing
it runs the package's `declare`/`setup`).

**Backends** — `dal/<capability>/<provider>.py` + `dal/<capability>/stub.py`.
Real named after the provider (`acme`, `tomtom`, `ical`, `webos`); fake always
`stub.py`. One `Protocol` per capability in `dal/interfaces.py`; type the resolved
module with it, not `ModuleType`.

**DB** — `dal/sqlite.py` owns schema + queries and takes an injected `connection`
(`ctx.api.connection`); call `sqlite.init_db(...)` from `setup()`. Omit the file
if the plugin persists nothing.

**Configs** — default (`src/plugins/…`) selects the real backend; test
(`tests/fixture/…`) selects `stub`.

## Choosing a backend pattern

Both resolve the dotted path via `column_to_value("module", …)`; they differ only
in where the backend is declared.

**A — `setting backend` in the plugin's own config** (`example`, `calendar`,
`travel`): one setting per capability, resolved in `setup()`.
```
# example.orc
setting foo_backend  orc_extras.example.dal.foo.acme
# setup(): m.column_to_value("module", s.foo_backend) -> stored on Runtime, typed FooService
```

**B — `--backend` on the `plugin` line** (`lgtv`): for a plugin with no config
file of its own; fetched via `orc.config.plugin_for(...).backend`.
```
plugin 'Pair LG TV' orc_extras.lgtv --function plugins.pair_tv --section device --backend orc_extras.lgtv.dal.tv.webos
```

Prefer A unless the backend naturally belongs to a `plugin` line.

## Value coercion

By **column name** (`column_to_value`), not the field annotation. `value` →
`int` when all-digits; so `widget <name> <value>` gives `Widget(name, value=int)`
free. Other names (`size`, `minutes`) stay strings — convert in `setup()`
(`int(e.minutes)`).

> **TODO:** make casting a per-plugin option (annotation-driven, or a per-column
> cast passed with the serializers) so a `size` column yields `int` without
> touching core or manual `int()`.
