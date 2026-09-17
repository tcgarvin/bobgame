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


class EntityIntent(BaseModel, frozen=True):
    """Base class for every per-entity intent submitted for a tick."""

    entity_id: str


class MoveIntent(EntityIntent, frozen=True):
    """A validated move intent from an entity."""

    direction: Direction


class CollectIntent(EntityIntent, frozen=True):
    """Intent to collect items from an object at the entity's position.

    Berry bushes have binary state: collecting always takes the single berry.
    """

    object_id: str | None = None  # If None, collect from any object at position
    item_type: str = "berry"


class EatIntent(EntityIntent, frozen=True):
    """Intent to consume items from inventory."""

    item_type: str
    amount: int = 1


class AttackIntent(EntityIntent, frozen=True):
    """Intent to attack an adjacent entity."""

    target_entity_id: str


class ExtractIntent(EntityIntent, frozen=True):
    """Intent to chop a tree or mine a rock on the same or an adjacent tile."""

    object_id: str


class PickupIntent(EntityIntent, frozen=True):
    """Intent to take items from an item_pile on the entity's own tile."""

    kind: str
    amount: int = 1


class WithdrawIntent(EntityIntent, frozen=True):
    """Intent to take items out of a chest on the same or an adjacent tile."""

    object_id: str
    kind: str
    amount: int = 1


class DropIntent(EntityIntent, frozen=True):
    """Intent to drop items onto the entity's own tile as an item_pile."""

    kind: str
    amount: int = 1


class DepositIntent(EntityIntent, frozen=True):
    """Intent to put items into a chest on the same or an adjacent tile."""

    object_id: str
    kind: str
    amount: int = 1


class CraftIntent(EntityIntent, frozen=True):
    """Intent to craft a recipe from inventory materials."""

    recipe: str


class EquipIntent(EntityIntent, frozen=True):
    """Intent to wield an inventory item, or unequip with an empty kind."""

    kind: str = ""


class PlaceIntent(EntityIntent, frozen=True):
    """Intent to place a placeable item on the adjacent tile in `direction`.

    `direction` is None only for ground-layer kinds (road, floors), which are
    laid on the placer's own tile; structures always name a direction.
    """

    kind: str
    direction: Direction | None = None


class WriteNoteIntent(EntityIntent, frozen=True):
    """Intent to write (or clear) one slot of a message board."""

    object_id: str
    slot: int
    title: str = ""
    text: str = ""


class RestIntent(EntityIntent, frozen=True):
    """Intent to rest on a bed on the same or an adjacent tile."""

    object_id: str


# Channels other entities can hear; "thought" only reaches the viewer.
LOCAL_CHANNEL = "local"
SHOUT_CHANNEL = "shout"
THOUGHT_CHANNEL = "thought"
AUDIBLE_CHANNELS: frozenset[str] = frozenset({LOCAL_CHANNEL, SHOUT_CHANNEL})
SAY_CHANNELS: frozenset[str] = AUDIBLE_CHANNELS | frozenset({THOUGHT_CHANNEL})


class SayIntent(EntityIntent, frozen=True):
    """Intent to speak on a channel ("local", "shout" or "thought")."""

    text: str
    channel: str = "local"


class WaitIntent(EntityIntent, frozen=True):
    """Intent to do nothing this tick (still counts as the entity's intent)."""


def chebyshev_distance(a: Position, b: Position) -> int:
    """Chebyshev (8-connected) distance between two positions."""
    return max(abs(a.x - b.x), abs(a.y - b.y))


def is_adjacent(a: Position, b: Position) -> bool:
    """True when b is one of the 8 neighbours of a (not the same tile)."""
    return a != b and chebyshev_distance(a, b) <= 1


def is_same_or_adjacent(a: Position, b: Position) -> bool:
    """True when b is a's tile or one of its 8 neighbours."""
    return chebyshev_distance(a, b) <= 1
