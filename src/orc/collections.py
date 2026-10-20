"""Shared containers, and the polyomino packer behind the scene page.

`pack` fits polyominoes of given sizes into a fixed-width grid.

Exact cover solved by the `dlx` package (Knuth's Algorithm X over dancing links):
each piece is a primary column, covered exactly once; each grid cell is a secondary
column, covered at most once, which is what lets a packing leave cells empty. A row
is one (piece, shape, position) choice. Knuth's own worked example is pentominoes;
nothing here cares whether a shape is a rectangle.
"""

import threading
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sized
from enum import StrEnum
from functools import cache
from itertools import count
from typing import Any, NamedTuple

import dlx

type _Cells = frozenset[tuple[int, int]]

_NEIGHBOURS = ((1, 0), (-1, 0), (0, 1), (0, -1))


class LockedDict[K, V]:
    def __init__(self, initial: Mapping[K, V] | None = None) -> None:
        self._lock = threading.Lock()
        self._data: dict[K, V] = dict(initial) if initial else {}

    def __setitem__(self, key: K, value: V) -> None:
        with self._lock:
            self._data[key] = value

    def get(self, key: K, default: V | None = None) -> V | None:
        with self._lock:
            return self._data.get(key, default)

    def pop(self, key: K, default: V | None = None) -> V | None:
        with self._lock:
            return self._data.pop(key, default)

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


class LockedDeque[T]:
    def __init__(self, maxlen: int) -> None:
        self._lock = threading.Lock()
        self._data: deque[T] = deque(maxlen=maxlen)

    def append(self, item: T) -> None:
        with self._lock:
            self._data.append(item)

    def appendleft(self, item: T) -> None:
        with self._lock:
            self._data.appendleft(item)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def snapshot(self) -> list[T]:
        with self._lock:
            return list(self._data)


class _Corner(StrEnum):
    TOP_LEFT = "tl"
    TOP_RIGHT = "tr"
    BOTTOM_LEFT = "bl"
    BOTTOM_RIGHT = "br"


_DIAGONALS = ((1, 1, _Corner.TOP_LEFT), (-1, 1, _Corner.TOP_RIGHT), (1, -1, _Corner.BOTTOM_LEFT), (-1, -1, _Corner.BOTTOM_RIGHT))


class ConcaveCorner(NamedTuple):
    x: int
    y: int
    corner: _Corner


class Box(NamedTuple):
    x: int
    y: int
    width: int
    height: int


def where[V](items: Mapping[str, V], **kwargs: Any) -> dict[str, V]:
    return {k: v for k, v in items.items() if all(getattr(v, attr) == val for attr, val in kwargs.items())}


def of_type[T](items: Iterable[object], cls: type[T]) -> Iterator[T]:
    return (item for item in items if isinstance(item, cls))


def pack[K](width: int, items: Mapping[K, Sized]) -> list[tuple[K, _Cells]]:
    keys = list(items)
    sizes = [len(item) for item in items.values()]
    if not sizes:
        return []

    # the tightest grid first; every piece fits as a vertical line, so sum(sizes) rows always packs
    for height in range(-(-sum(sizes) // width), sum(sizes) + 1):
        # number of grid sizes that must be filled over a grid of HxW
        constraints = [(i, dlx.DLX.PRIMARY) for i in range(len(sizes))]
        constraints += [(c, dlx.DLX.SECONDARY) for c in range(len(sizes), len(sizes) + height * width)]

        problem, choices = dlx.DLX(constraints), {}
        for i, n in enumerate(sizes):
            for shape in _shapes(n):
                w, h = _dimensions(shape)
                for y in range(height - h + 1):
                    for x in range(width - w + 1):
                        # possible placements on the grid for everything
                        xy_cells = frozenset((x + dx, y + dy) for dx, dy in shape)
                        flat_cells = (len(sizes) + b * width + a for a, b in xy_cells)
                        choices[problem.appendRow([i, *flat_cells])] = (i, xy_cells)

        solution = next(problem.solve(columnselector=_biggest_first, columnselectoruserdata=sizes), None)
        if solution:
            return [(keys[i], xy_cells) for i, xy_cells in sorted((choices[row] for row in solution), key=lambda chosen: chosen[0])]
    raise ValueError(f"grid: no packing for {sizes} in {width} columns")


def boxes(cells: _Cells) -> list[Box]:
    # the rectangles that, overlapped, paint the whole shape across the gaps between its cells: one per
    # horizontal stretch, one per vertical stretch of two or more to bridge the gap between them, and
    # one 2x2 block wherever the two gaps cross, which no straight rectangle reaches
    found = []
    for x, y in cells:
        if (x - 1, y) not in cells:
            found.append(Box(x, y, next(n for n in count(1) if (x + n, y) not in cells), 1))
        if (x, y - 1) not in cells and (x, y + 1) in cells:
            found.append(Box(x, y, 1, next(n for n in count(1) if (x, y + n) not in cells)))
        if {(x + 1, y), (x, y + 1), (x + 1, y + 1)} <= cells:
            found.append(Box(x, y, 2, 2))
    return found


def concave_corners(cells: _Cells) -> list[ConcaveCorner]:
    # a concave corner is rounded from inside the empty cell diagonal to it, in that cell's nearest corner
    return [
        ConcaveCorner(x + dx, y + dy, corner)
        for x, y in cells
        for dx, dy, corner in _DIAGONALS
        if {(x + dx, y), (x, y + dy)} <= cells and (x + dx, y + dy) not in cells
    ]


def _biggest_first(problem: dlx.DLX, sizes: list[int]) -> int:
    """Knuth branches on the fewest candidates alone, which ranks a rigid 1x1 above a
    big piece carrying many shapes and strands the big one in leftover space."""

    def rank(constraint: int) -> tuple[int, int]:
        return -sizes[problem.N[constraint]], problem.S[constraint]

    best = node = problem.R[problem.header]
    while (node := problem.R[node]) != problem.header:
        best = min(best, node, key=rank)
    return best


@cache
def _shapes(n: int) -> tuple[_Cells, ...]:
    """Every fixed polyomino of n cells as offsets from its top-left corner, squarest
    first, then the least empty space in its bounding box. Rotations and reflections
    are distinct shapes: the page has an orientation."""
    grown: set[_Cells] = {frozenset({(0, 0)})}
    for _ in range(n - 1):
        grown = {
            _normalised(shape | {(x + dx, y + dy)})
            for shape in grown
            for x, y in shape
            for dx, dy in _NEIGHBOURS
            if (x + dx, y + dy) not in shape
        }
    return tuple(sorted(grown, key=_compactness_key))


def _normalised(cells: _Cells) -> _Cells:
    left, top = min(x for x, _ in cells), min(y for _, y in cells)
    return frozenset((x - left, y - top) for x, y in cells)


def _compactness_key(cells: _Cells) -> tuple[int, int, tuple[tuple[int, int], ...]]:
    # (how far the bounding box is from square, its empty cells, the sorted cells): smaller sorts
    # first on each term in turn, and the cells make equal shapes order the same way on every boot
    w, h = _dimensions(cells)
    return abs(w - h), w * h - len(cells), tuple(sorted(cells))


def _dimensions(cells: _Cells) -> tuple[int, int]:
    return max(x for x, _ in cells) + 1, max(y for _, y in cells) + 1
