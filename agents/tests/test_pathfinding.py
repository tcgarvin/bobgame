"""A* behaviour: straight lines, diagonal blocking, unknown-tile cost."""

from __future__ import annotations

from agents import world_pb2 as pb
from agents.jev_agent.geometry import Coord
from agents.jev_agent.pathfinding import (
    NO_PATH,
    find_path,
    legal_directions,
    next_step,
    path_length,
)


class GridView:
    """A hand-built terrain view for pathfinding tests."""

    def __init__(self, known: set[Coord], blocked: set[Coord], size: int = 10) -> None:
        self._known = known
        self._blocked = blocked
        self._size = size

    def is_known(self, position: Coord) -> bool:
        return position in self._known

    def is_walkable(self, position: Coord) -> bool:
        x, y = position
        if not (0 <= x < self._size and 0 <= y < self._size):
            return False
        return position not in self._blocked


def open_grid(size: int = 10) -> GridView:
    """A fully observed, fully walkable `size` x `size` grid."""
    known = {(x, y) for x in range(size) for y in range(size)}
    return GridView(known, set())


def test_straight_east_path_has_one_step_per_tile() -> None:
    view = open_grid()
    path = find_path(view, (0, 0), (3, 0))
    assert path == [(1, 0), (2, 0), (3, 0)]
    assert path_length(view, (0, 0), (3, 0)) == 3


def test_diagonal_is_preferred_when_it_is_shorter() -> None:
    view = open_grid()
    assert path_length(view, (0, 0), (3, 3)) == 3
    assert next_step(view, (0, 0), (3, 3)) == pb.SOUTHEAST


def test_diagonal_blocked_when_either_cardinal_component_is_blocked() -> None:
    view = GridView(
        known={(x, y) for x in range(5) for y in range(5)},
        blocked={(1, 0)},
    )
    # SE from (0,0) needs both (1,0) and (0,1) walkable, so blocking either is enough.
    assert pb.SOUTHEAST not in legal_directions(view, (0, 0))
    assert pb.SOUTH in legal_directions(view, (0, 0))
    open_view = GridView({(x, y) for x in range(5) for y in range(5)}, set())
    assert pb.SOUTHEAST in legal_directions(open_view, (0, 0))


def test_path_routes_around_a_wall() -> None:
    wall = {(2, y) for y in range(0, 5)}
    view = GridView({(x, y) for x in range(6) for y in range(6)}, wall)
    path = find_path(view, (0, 0), (4, 0))
    assert path, "a route around the wall should exist"
    assert all(step not in wall for step in path)


def test_unreachable_target_reports_no_path() -> None:
    view = GridView(
        known={(x, y) for x in range(5) for y in range(5)},
        blocked={(1, 0), (1, 1), (0, 1)},
        size=5,
    )
    assert find_path(view, (0, 0), (4, 4)) == []
    assert path_length(view, (0, 0), (4, 4)) == NO_PATH
    assert next_step(view, (0, 0), (4, 4)) == pb.DIRECTION_UNSPECIFIED


def test_unknown_tiles_cost_more_than_known_ones() -> None:
    # A known detour of four steps beats a two-step hop through the unknown
    # only because unknown tiles cost three.
    known = {(0, 0), (0, 1), (1, 1), (2, 1), (2, 0)}
    view = GridView(known, set())
    path = find_path(view, (0, 0), (2, 0))
    assert (1, 0) not in path, "should avoid the single unknown tile"


def test_stop_adjacent_finishes_next_to_a_blocking_target() -> None:
    view = GridView({(x, y) for x in range(6) for y in range(6)}, {(3, 0)})
    path = find_path(view, (0, 0), (3, 0), stop_adjacent=True)
    assert path and path[-1] != (3, 0)
    assert path_length(view, (2, 0), (3, 0), stop_adjacent=True) == 0


def test_already_at_goal_returns_empty_path() -> None:
    view = open_grid()
    assert find_path(view, (2, 2), (2, 2)) == []
    assert path_length(view, (2, 2), (2, 2)) == 0
