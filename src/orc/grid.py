"""Pack rectangles of given sizes into a fixed-width grid.

Exact cover solved by the `dlx` package (Knuth's Algorithm X over dancing links):
each rectangle is a primary column, covered exactly once; each grid cell is a
secondary column, covered at most once, which is what lets a packing leave cells
empty. A row is one (rectangle, shape, position) choice.
"""

from collections.abc import Mapping, Sized
from functools import cache
from typing import NamedTuple

import dlx


class Placement(NamedTuple):
    width: int
    height: int
    x: int
    y: int


@cache
def shapes(n: int) -> tuple[tuple[int, int], ...]:
    pairs = {(w, -(-n // w)) for w in range(1, n + 1)}
    return tuple(sorted(pairs, key=lambda s: (abs(s[0] - s[1]), s[0] * s[1])))


def pack[K](width: int, items: Mapping[K, Sized]) -> list[tuple[K, Placement]]:
    keys = list(items)
    sizes = [len(item) for item in items.values()]
    if not sizes:
        return []

    height = -(-sum(sizes) // width) + 2  # spare rows: a size whose only fitting shapes waste cells needs both

    # number of grid sizes that must be filled over a grid of HxW
    constraints = [(i, dlx.DLX.PRIMARY) for i in range(len(sizes))]
    constraints += [(c, dlx.DLX.SECONDARY) for c in range(len(sizes), len(sizes) + height * width)]

    problem, choices = dlx.DLX(constraints), {}
    for i, n in enumerate(sizes):
        for w, h in shapes(n):
            for y in range(height - h + 1):
                for x in range(width - w + 1):
                    # possible placements on the grid for everything
                    covered = (len(sizes) + b * width + a for b in range(y, y + h) for a in range(x, x + w))
                    choices[problem.appendRow([i, *covered])] = (i, Placement(w, h, x, y))

    solution = next(problem.solve(columnselector=_biggest_first, columnselectoruserdata=sizes), None)
    if solution is None:
        raise ValueError(f"grid: no packing for {sizes} in {width} columns")
    return [(keys[i], placement) for i, placement in sorted(choices[row] for row in solution)]


def _biggest_first(problem: dlx.DLX, sizes: list[int]) -> int:
    """Knuth branches on the fewest candidates alone, which ranks a rigid 1x1 above a
    big rectangle carrying many shapes and strands the big one in leftover space."""

    def rank(constraint: int) -> tuple[int, int]:
        return -sizes[problem.N[constraint]], problem.S[constraint]

    best = node = problem.R[problem.header]
    while (node := problem.R[node]) != problem.header:
        best = min(best, node, key=rank)
    return best
