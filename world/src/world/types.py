"""Core types for the world simulation."""

from enum import IntEnum

from pydantic import BaseModel


class Direction(IntEnum):
    """8-direction movement enum matching proto Direction."""

    NORTH = 1
    NORTHEAST = 2
    EAST = 3
    SOUTHEAST = 4
    SOUTH = 5
    SOUTHWEST = 6
    WEST = 7
    NORTHWEST = 8


# Direction deltas for movement calculation
# Coordinate system: +X is East, +Y is South
DIRECTION_DELTAS: dict[Direction, tuple[int, int]] = {
    Direction.NORTH: (0, -1),
    Direction.NORTHEAST: (1, -1),
    Direction.EAST: (1, 0),
    Direction.SOUTHEAST: (1, 1),
    Direction.SOUTH: (0, 1),
    Direction.SOUTHWEST: (-1, 1),
    Direction.WEST: (-1, 0),
    Direction.NORTHWEST: (-1, -1),
}


# For diagonal blocking check: maps diagonal to its two cardinal components
DIAGONAL_COMPONENTS: dict[Direction, tuple[Direction, Direction]] = {
    Direction.NORTHEAST: (Direction.NORTH, Direction.EAST),
    Direction.SOUTHEAST: (Direction.SOUTH, Direction.EAST),
    Direction.SOUTHWEST: (Direction.SOUTH, Direction.WEST),
    Direction.NORTHWEST: (Direction.NORTH, Direction.WEST),
}


class Position(BaseModel, frozen=True):
    """Immutable 2D tile coordinate."""

    x: int
    y: int

    def __add__(self, other: "Position") -> "Position":
        return Position(x=self.x + other.x, y=self.y + other.y)

    def offset(self, direction: Direction) -> "Position":
        """Return new position offset by direction."""
        dx, dy = DIRECTION_DELTAS[direction]
        return Position(x=self.x + dx, y=self.y + dy)

    def __hash__(self) -> int:
        return hash((self.x, self.y))

    def __str__(self) -> str:
        return f"({self.x}, {self.y})"

    def __repr__(self) -> str:
        return f"Position(x={self.x}, y={self.y})"


class MoveIntent(BaseModel, frozen=True):
    """A validated move intent from an entity."""

    entity_id: str
    direction: Direction


class CollectIntent(BaseModel, frozen=True):
    """Intent to collect items from an object at the entity's position.

    Berry bushes have binary state: collecting always takes the single berry.
    """

    entity_id: str
    object_id: str | None = None  # If None, collect from any object at position
    item_type: str = "berry"


class EatIntent(BaseModel, frozen=True):
    """Intent to consume items from inventory."""

    entity_id: str
    item_type: str
    amount: int = 1
