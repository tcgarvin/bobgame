"""A* pathfinding over whatever the agent remembers of the map.

Eight-connected, using the world's diagonal-blocking rule: a diagonal step is
only legal when both of its cardinal components are walkable. Tiles the agent
has never observed are passable but cost `UNKNOWN_TILE_COST`, so a path through
the unknown is taken only when it is clearly shorter than a known detour, which
is what makes exploration happen.
"""

from __future__ import annotations

import heapq
from typing import Protocol

from .. import world_pb2 as pb
from .geometry import (
    DIAGONAL_COMPONENTS,
    DIRECTION_DELTAS,
    NO_DIRECTION,
    ORDERED_DIRECTIONS,
    Coord,
    chebyshev,
    direction_between,
    offset,
)

KNOWN_TILE_COST = 1
UNKNOWN_TILE_COST = 3
DIAGONAL_SURCHARGE = 0.001  # breaks ties in favour of straight lines

# `path_length` returns this when the target cannot be reached.
NO_PATH = -1

# Kept small: pathfinding runs synchronously inside the tick loop, several
# times per tick, and must never stall lease renewal or the intent deadline.
DEFAULT_MAX_NODES = 2_500


class TerrainView(Protocol):
    """The slice of the world model that pathfinding needs."""

    def is_walkable(self, position: Coord) -> bool:
        """Whether an entity may stand on `position`. Unknown tiles are walkable."""

    def is_known(self, position: Coord) -> bool:
        """Whether this tile has ever been observed."""


def _step_cost(view: TerrainView, position: Coord, diagonal: bool) -> float:
    cost = KNOWN_TILE_COST if view.is_known(position) else UNKNOWN_TILE_COST
    return cost + (DIAGONAL_SURCHARGE if diagonal else 0.0)


def _can_step(view: TerrainView, origin: Coord, direction: pb.Direction) -> bool:
    target = offset(origin, direction)
    if not view.is_walkable(target):
        return False
    if direction in DIAGONAL_COMPONENTS:
        first, second = DIAGONAL_COMPONENTS[direction]
        if not view.is_walkable(offset(origin, first)):
            return False
        if not view.is_walkable(offset(origin, second)):
            return False
    return True


def legal_directions(view: TerrainView, origin: Coord) -> list[pb.Direction]:
    """The directions an entity at `origin` may legally move in, in compass order."""
    return [d for d in ORDERED_DIRECTIONS if _can_step(view, origin, d)]


def find_path(
    view: TerrainView,
    start: Coord,
    goal: Coord,
    *,
    stop_adjacent: bool = False,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> list[Coord]:
    """A* from `start` to `goal`, returning the steps after `start`.

    Args:
        view: terrain knowledge to plan over.
        start: where the entity is now.
        goal: where it wants to be.
        stop_adjacent: finish on any tile adjacent to `goal` instead of on it.
            Use this for goals that block movement, such as trees and rocks.
        max_nodes: search budget; an exhausted budget yields an empty path.

    Returns:
        The coordinates to step through, excluding `start`. Empty when `start`
        already satisfies the goal or no route was found.
    """

    def reached(position: Coord) -> bool:
        if stop_adjacent:
            return chebyshev(position, goal) <= 1
        return position == goal

    if reached(start):
        return []

    open_heap: list[tuple[float, int, Coord]] = []
    counter = 0
    heapq.heappush(open_heap, (0.0, counter, start))
    came_from: dict[Coord, Coord] = {}
    best_cost: dict[Coord, float] = {start: 0.0}
    expanded = 0

    while open_heap:
        _, _, current = heapq.heappop(open_heap)
        if reached(current):
            return _reconstruct(came_from, current)
        expanded += 1
        if expanded > max_nodes:
            break
        current_cost = best_cost[current]
        for direction in ORDERED_DIRECTIONS:
            if not _can_step(view, current, direction):
                continue
            dx, dy = DIRECTION_DELTAS[direction]
            neighbour = (current[0] + dx, current[1] + dy)
            diagonal = dx != 0 and dy != 0
            tentative = current_cost + _step_cost(view, neighbour, diagonal)
            if tentative >= best_cost.get(neighbour, float("inf")):
                continue
            best_cost[neighbour] = tentative
            came_from[neighbour] = current
            heuristic = float(chebyshev(neighbour, goal))
            if stop_adjacent:
                heuristic = max(0.0, heuristic - 1.0)
            counter += 1
            heapq.heappush(open_heap, (tentative + heuristic, counter, neighbour))

    return []


def _reconstruct(came_from: dict[Coord, Coord], end: Coord) -> list[Coord]:
    path = [end]
    while path[-1] in came_from:
        path.append(came_from[path[-1]])
    path.reverse()
    return path[1:]


def next_step(
    view: TerrainView,
    start: Coord,
    goal: Coord,
    *,
    stop_adjacent: bool = False,
) -> pb.Direction:
    """The Direction of the first step toward `goal`, or `NO_DIRECTION`."""
    path = find_path(view, start, goal, stop_adjacent=stop_adjacent)
    if not path:
        return NO_DIRECTION
    return direction_between(start, path[0])


def path_length(
    view: TerrainView,
    start: Coord,
    goal: Coord,
    *,
    stop_adjacent: bool = False,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> int:
    """Number of steps to `goal`, 0 when already there, `NO_PATH` when unreachable.

    An exhausted `max_nodes` budget reports `NO_PATH`, so a caller that uses a
    small budget is asking "is this nearby and reachable", not "is this
    reachable at all".
    """
    if stop_adjacent:
        if chebyshev(start, goal) <= 1:
            return 0
    elif start == goal:
        return 0
    path = find_path(
        view, start, goal, stop_adjacent=stop_adjacent, max_nodes=max_nodes
    )
    if not path:
        return NO_PATH
    return len(path)
