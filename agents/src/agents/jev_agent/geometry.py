"""Directions, offsets, and distance helpers shared by the jev_agent modules.

`+x` is east and `+y` is south, matching `proto.Direction` and the world server.
"""

from __future__ import annotations

from .. import world_pb2 as pb

Coord = tuple[int, int]

# Direction enum value -> (dx, dy). +y is south.
DIRECTION_DELTAS: dict[pb.Direction, Coord] = {
    pb.NORTH: (0, -1),
    pb.NORTHEAST: (1, -1),
    pb.EAST: (1, 0),
    pb.SOUTHEAST: (1, 1),
    pb.SOUTH: (0, 1),
    pb.SOUTHWEST: (-1, 1),
    pb.WEST: (-1, 0),
    pb.NORTHWEST: (-1, -1),
}

DELTA_TO_DIRECTION: dict[Coord, pb.Direction] = {
    delta: direction for direction, delta in DIRECTION_DELTAS.items()
}

DIRECTION_NAMES: dict[pb.Direction, str] = {
    pb.NORTH: "N",
    pb.NORTHEAST: "NE",
    pb.EAST: "E",
    pb.SOUTHEAST: "SE",
    pb.SOUTH: "S",
    pb.SOUTHWEST: "SW",
    pb.WEST: "W",
    pb.NORTHWEST: "NW",
}

NAME_TO_DIRECTION: dict[str, pb.Direction] = {
    name: direction for direction, name in DIRECTION_NAMES.items()
}

# Direction value -> the two cardinal components a diagonal move passes between.
DIAGONAL_COMPONENTS: dict[pb.Direction, tuple[pb.Direction, pb.Direction]] = {
    pb.NORTHEAST: (pb.NORTH, pb.EAST),
    pb.SOUTHEAST: (pb.SOUTH, pb.EAST),
    pb.SOUTHWEST: (pb.SOUTH, pb.WEST),
    pb.NORTHWEST: (pb.NORTH, pb.WEST),
}

# `pb.DIRECTION_UNSPECIFIED` (0) is the "no direction" sentinel throughout.
NO_DIRECTION: pb.Direction = pb.DIRECTION_UNSPECIFIED

ORDERED_DIRECTIONS: tuple[pb.Direction, ...] = (
    pb.NORTH,
    pb.NORTHEAST,
    pb.EAST,
    pb.SOUTHEAST,
    pb.SOUTH,
    pb.SOUTHWEST,
    pb.WEST,
    pb.NORTHWEST,
)


def chebyshev(a: Coord, b: Coord) -> int:
    """Chebyshev (8-connected) distance, the world's notion of adjacency."""
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def is_adjacent(a: Coord, b: Coord) -> bool:
    """True when `b` is one of the eight neighbours of `a`."""
    return chebyshev(a, b) == 1


def same_or_adjacent(a: Coord, b: Coord) -> bool:
    """True when `b` is `a` or one of its eight neighbours."""
    return chebyshev(a, b) <= 1


def direction_name(direction: pb.Direction) -> str:
    """Short compass name for a Direction value ("?" when unspecified)."""
    return DIRECTION_NAMES.get(direction, "?")


def offset(position: Coord, direction: pb.Direction) -> Coord:
    """The neighbouring coordinate one step in `direction`."""
    dx, dy = DIRECTION_DELTAS[direction]
    return (position[0] + dx, position[1] + dy)


def direction_between(origin: Coord, target: Coord) -> pb.Direction:
    """Direction from `origin` to an adjacent `target`, or `NO_DIRECTION`."""
    delta = (target[0] - origin[0], target[1] - origin[1])
    return DELTA_TO_DIRECTION.get(delta, NO_DIRECTION)
