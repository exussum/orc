import threading
from collections.abc import Callable, Iterator, Mapping
from types import SimpleNamespace
from typing import Any

from mistletoe.block_token import Heading, Table


class LockedDict[K, V]:
    def __init__(self, initial: Mapping[K, V] | None = None) -> None:
        self._lock = threading.Lock()
        self._data: dict[K, V] = dict(initial) if initial else {}

    def __contains__(self, key: K) -> bool:
        with self._lock:
            return key in self._data

    def __getitem__(self, key: K) -> V:
        with self._lock:
            return self._data[key]

    def __setitem__(self, key: K, value: V) -> None:
        with self._lock:
            self._data[key] = value

    def get(self, key: K, default: V | None = None) -> V | None:
        with self._lock:
            return self._data.get(key, default)

    def pop(self, key: K, default: V | None = None) -> V | None:
        with self._lock:
            return self._data.pop(key, default)

    def get_or_set(self, key: K, factory: Callable[[], V]) -> V:
        with self._lock:
            if key in self._data:
                return self._data[key]
            value = factory()
            self._data[key] = value
            return value

    def update(self, key: K, fn: Callable[[V | None], V | None]) -> V | None:
        with self._lock:
            new = fn(self._data.get(key))
            if new is None:
                return None
            self._data[key] = new
            return new

    def values(self) -> list[V]:
        with self._lock:
            return list(self._data.values())

    def copy(self) -> dict[K, V]:
        with self._lock:
            return dict(self._data)


def parse_kv(val: str | None) -> dict[str, str]:
    return dict(pair.split("=", 1) for pair in (val or "").split())


def where[V](items: Mapping[str, V], **kwargs: Any) -> dict[str, V]:
    return {k: v for k, v in items.items() if all(getattr(v, attr) == val for attr, val in kwargs.items())}


def doc_to_sub_tables(
    doc: Any,
    section: str,
    columns: tuple[str, ...] | int,
    *,
    cast: Callable[[str, Any], Any] | None,
) -> Iterator[tuple[Any, list[Any]]]:
    col_names = columns if isinstance(columns, tuple) else None
    n_cols = len(columns) if isinstance(columns, tuple) else columns
    type: Any = None
    result: list[Any] | None = None
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
        result.append(row)  # type: ignore[union-attr]  # result is a list once the first non-empty type row is seen

    if result:
        yield type, result


def doc_to_table(doc: Any, section: str, columns: int) -> tuple[tuple[Any, ...], ...]:
    # Heading store their contents in a subsequent child element
    # https://github.com/miyuchina/mistletoe/issues/99
    idx = next(
        # mistletoe Heading.children is Iterable | None but always populated here
        (i for (i, e) in enumerate(doc.children) if isinstance(e, Heading) and e.children[0].content == section),  # type: ignore[index]
        None,
    )
    if idx is None:
        raise ValueError(f"Section '{section}' not found in document")

    markdown_table = next((e for e in doc.children[idx + 1 :] if isinstance(e, Table)), None)
    if markdown_table is None:
        raise ValueError(f"No table found under section '{section}'")

    rows = list(markdown_table.children)  # type: ignore[arg-type]  # mistletoe Table.children is Iterable | None but always populated here
    # TableRow.children always populated
    if invalid := [(i, len(row.children)) for i, row in enumerate(rows) if len(row.children) != columns]:  # type: ignore[arg-type]
        bad_rows = ", ".join(str(i) for i, _ in invalid)
        raise ValueError(f"Expected {columns} columns in section '{section}', but rows {bad_rows} have the wrong number")

    # mistletoe TableCell/TableRow children populated at runtime
    return tuple(
        tuple(c.children[0].content if c.children else None for c in e.children)  # type: ignore[index,union-attr]
        + (None,) * (columns - len(e.children))  # type: ignore[arg-type]
        for e in rows
    )
