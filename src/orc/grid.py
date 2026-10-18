"""Pack named boxes of items into a fixed-width grid as rectangles.

Exact cover solved by the `dlx` package (Knuth's Algorithm X over dancing links):
each box is a primary column, covered exactly once; each grid cell is a secondary
column, covered at most once, which is what lets a packing leave cells empty.
A row is one (box, shape, position) choice.
"""

from dataclasses import dataclass
from functools import cache
from typing import Any

import dlx

PRIME_SLACK = 2


@dataclass
class Box:
    name: str
    items: Any


@dataclass
class Placement:
    box: Box
    shape: tuple[int, int]
    x: int
    y: int


@cache
def boxes(n: int) -> tuple[tuple[int, int], ...]:
    """Shapes holding n items, squarest first, then fewest placeholders. A prime only
    tiles as 1xN, so it may also take up to PRIME_SLACK cells to reach a rectangle."""
    slack = PRIME_SLACK if n > 2 and all(n % d for d in range(2, n)) else 0
    shapes = ((d, size // d) for size in range(n, n + slack + 1) for d in range(1, size + 1) if size % d == 0)
    return tuple(sorted(dict.fromkeys(shapes), key=lambda s: (abs(s[0] - s[1]), s[0] * s[1])))


def pack(height: int, width: int, inputs: list[Box]) -> list[Placement] | None:
    if not inputs:
        return []
    cells = range(len(inputs), len(inputs) + height * width)
    problem = dlx.DLX([(i, dlx.DLX.PRIMARY) for i in range(len(inputs))] + [(c, dlx.DLX.SECONDARY) for c in cells])
    rows = {}
    for index, box in enumerate(inputs):
        for w, h in boxes(len(box.items)):
            for y in range(height - h + 1):
                for x in range(width - w + 1):
                    covered = [len(inputs) + b * width + a for b in range(y, y + h) for a in range(x, x + w)]
                    rows[problem.appendRow([index, *covered])] = (index, Placement(box, (w, h), x, y))
    weights = [len(box.items) for box in inputs]
    solution = next(problem.solve(columnselector=_biggest_box_first, columnselectoruserdata=weights), None)
    if solution is None:
        return None
    return [placement for _, placement in sorted((rows[row] for row in solution), key=lambda pair: pair[0])]


def _biggest_box_first(problem: dlx.DLX, weights: list[int]) -> int:
    """Knuth branches on the fewest candidates alone, which ranks a rigid 1x1 above a
    big box carrying many shapes and strands the big one in leftover space."""
    columns, node = [], problem.R[problem.header]
    while node != problem.header:
        columns.append(node)
        node = problem.R[node]
    return min(columns, key=lambda c: (-weights[problem.N[c]], problem.S[c]))
