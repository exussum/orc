import threading

from mistletoe.block_token import Heading, Table


class LockedDict:
    def __init__(self, initial=None):
        self._lock = threading.Lock()
        self._data = dict(initial) if initial else {}

    def __contains__(self, key):
        with self._lock:
            return key in self._data

    def __getitem__(self, key):
        with self._lock:
            return self._data[key]

    def __setitem__(self, key, value):
        with self._lock:
            self._data[key] = value

    def get(self, key, default=None):
        with self._lock:
            return self._data.get(key, default)

    def get_or_set(self, key, factory):
        with self._lock:
            if key in self._data:
                return self._data[key]
            value = factory()
            self._data[key] = value
            return value

    def update(self, key, fn):
        with self._lock:
            new = fn(self._data.get(key))
            if new is None:
                return None
            self._data[key] = new
            return new

    def copy(self):
        with self._lock:
            return dict(self._data)


def parse_kv(val):
    return dict(pair.split("=") for pair in (val or "").split())


def where(items, **kwargs):
    return {k: v for k, v in items.items() if all(getattr(v, attr) == val for attr, val in kwargs.items())}


def doc_to_sub_tables(doc, section, columns, *, cast):
    from types import SimpleNamespace

    col_names = columns if isinstance(columns, tuple) else None
    n_cols = len(columns) if col_names else columns
    type, result = None, None
    for e in doc_to_table(doc, section, n_cols):
        if e[0] != type and e[0]:
            if result:
                yield type, result
            type, result = e[0], []
        row = (
            SimpleNamespace(**{col_names[j].lower(): cast(col_names[j], e[j]) for j in range(1, len(col_names))})
            if col_names and cast
            else e
        )
        result.append(row)

    if result:
        yield type, result


def doc_to_table(doc, section, columns):
    # Heading store their contents in a subsequent child element
    # https://github.com/miyuchina/mistletoe/issues/99
    idx = next(
        (i for (i, e) in enumerate(doc.children) if isinstance(e, Heading) and e.children[0].content == section),
        None,
    )
    if idx is None:
        raise ValueError(f"Section '{section}' not found in document")

    markdown_table = next((e for e in doc.children[idx + 1 :] if isinstance(e, Table)), None)
    if markdown_table is None:
        raise ValueError(f"No table found under section '{section}'")

    rows = list(markdown_table.children)
    if invalid := [(i, len(row.children)) for i, row in enumerate(rows) if len(row.children) != columns]:
        bad_rows = ", ".join(str(i) for i, _ in invalid)
        raise ValueError(f"Expected {columns} columns in section '{section}', but rows {bad_rows} have the wrong number")

    return tuple(
        tuple(c.children[0].content if c.children else None for c in e.children) + (None,) * (columns - len(e.children)) for e in rows
    )
