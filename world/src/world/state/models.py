"""The frozen models the world is made of: the clock, tiles, entities, objects.

Every one is immutable (`frozen=True`); a change means a `.model_copy` or one
of the `with_*` helpers here. `World`, the one mutable container, lives beside
this in `world.py`.
"""

from pydantic import BaseModel

from ..terrain_types import FloorType
from ..types import Position

STATUS_BIT_DEAD = 1

# Entity types. Wolves are stopped by doors; everyone else opens them.
DEFAULT_ENTITY_TYPE = "default"
WOLF_ENTITY_TYPE = "wolf"

# The day clock (docs/10_metal_and_sleep.md, "The day"). The first two thirds
# of a day are daylight, the rest is night.
DEFAULT_DAY_LENGTH_TICKS = 300
NIGHT_START_NUMERATOR = 2
NIGHT_START_DENOMINATOR = 3


def night_start_tick(day_length: int) -> int:
    """First tick of the day that counts as night."""
    return day_length * NIGHT_START_NUMERATOR // NIGHT_START_DENOMINATOR


class WorldClock(BaseModel, frozen=True):
    """Where the world sits in its day/night cycle.

    The last three fields are the new moon (docs/14_new_moon_and_saves.md):
    whether tonight is one, the 0-based day of the next one (-1 when the world
    has no new moon at all) and, only on the tick a save is taken, that tick.
    """

    day: int
    tick_of_day: int
    day_length: int
    night: bool
    new_moon_tonight: bool = False
    next_new_moon_day: int = -1
    save_tick: int = 0


class Inventory(BaseModel, frozen=True):
    """Immutable inventory as item_type -> count mapping."""

    items: tuple[tuple[str, int], ...] = ()

    def count(self, item_type: str) -> int:
        """Get count of item type."""
        for k, v in self.items:
            if k == item_type:
                return v
        return 0

    def has(self, item_type: str, amount: int = 1) -> bool:
        """Check if inventory has at least amount of item_type."""
        return self.count(item_type) >= amount

    def add(self, item_type: str, amount: int = 1) -> "Inventory":
        """Return new inventory with added items."""
        new_items = dict(self.items)
        new_items[item_type] = new_items.get(item_type, 0) + amount
        return Inventory(items=tuple(new_items.items()))

    def remove(self, item_type: str, amount: int = 1) -> "Inventory":
        """Return new inventory with removed items.

        Raises:
            ValueError: If insufficient items to remove.
        """
        current = self.count(item_type)
        if current < amount:
            raise ValueError(f"Cannot remove {amount} {item_type}, only have {current}")
        new_items = dict(self.items)
        new_count = current - amount
        if new_count == 0:
            del new_items[item_type]
        else:
            new_items[item_type] = new_count
        return Inventory(items=tuple(new_items.items()))


class Tile(BaseModel, frozen=True):
    """Immutable tile properties."""

    position: Position
    walkable: bool = True
    opaque: bool = False
    floor_type: str = "stone"

    @classmethod
    def from_floor_type(cls, position: Position, floor_type: FloorType) -> "Tile":
        """Create a tile with properties derived from floor type.

        Args:
            position: Tile position.
            floor_type: FloorType enum value.

        Returns:
            Tile with walkable/opaque properties set based on floor type.
        """
        if not isinstance(floor_type, FloorType):
            raise TypeError(f"Expected FloorType, got {type(floor_type)}")

        return cls(
            position=position,
            walkable=floor_type.walkable,
            opaque=floor_type.opaque,
            floor_type=floor_type.value,
        )


class Entity(BaseModel, frozen=True):
    """Immutable entity state."""

    entity_id: str
    position: Position
    entity_type: str = "default"
    tags: tuple[str, ...] = ()
    status_bits: int = 0
    inventory: Inventory = Inventory()
    health: int = 20
    max_health: int = 20
    food: int = 80
    max_food: int = 100
    wielded: str = ""  # item kind currently wielded, "" if none
    alive: bool = True

    # Body clock (docs/10_metal_and_sleep.md, "Fatigue and sleep"). Wolves
    # never accumulate fatigue, so theirs stays at 0.
    fatigue: int = 0
    max_fatigue: int = 100
    asleep: bool = False
    # Object id of the bed slept on, "" for the ground (and while awake).
    sleeping_on: str = ""
    # True while the sleep was forced by exhaustion rather than chosen.
    collapsed: bool = False
    # The tick this entity's last conversation seat ended (docs/09, section 9).
    # It cannot be hailed again until `HAIL_COOLDOWN_TICKS` have passed; -1
    # means it has never sat in one.
    last_conversation_end_tick: int = -1

    def with_position(self, new_position: Position) -> "Entity":
        """Return copy with updated position."""
        return self.model_copy(update={"position": new_position})

    def with_inventory(self, new_inventory: Inventory) -> "Entity":
        """Return copy with updated inventory."""
        return self.model_copy(update={"inventory": new_inventory})

    def with_health(self, new_health: int) -> "Entity":
        """Return copy with health clamped to [0, max_health]."""
        clamped = max(0, min(self.max_health, new_health))
        return self.model_copy(update={"health": clamped})

    def with_food(self, new_food: int) -> "Entity":
        """Return copy with food clamped to [0, max_food]."""
        clamped = max(0, min(self.max_food, new_food))
        return self.model_copy(update={"food": clamped})

    def with_wielded(self, kind: str) -> "Entity":
        """Return copy with a different wielded item kind ("" for none)."""
        return self.model_copy(update={"wielded": kind})

    def with_fatigue(self, new_fatigue: int) -> "Entity":
        """Return copy with fatigue clamped to [0, max_fatigue]."""
        clamped = max(0, min(self.max_fatigue, new_fatigue))
        return self.model_copy(update={"fatigue": clamped})

    def as_asleep(self, sleeping_on: str, collapsed: bool = False) -> "Entity":
        """Return copy asleep on a bed (`sleeping_on`) or the ground ("")."""
        return self.model_copy(
            update={
                "asleep": True,
                "sleeping_on": sleeping_on,
                "collapsed": collapsed,
            }
        )

    def as_awake(self) -> "Entity":
        """Return copy awake and off whatever it was sleeping on."""
        return self.model_copy(
            update={"asleep": False, "sleeping_on": "", "collapsed": False}
        )

    def with_conversation_ended(self, tick: int) -> "Entity":
        """Return copy whose hail cooldown starts at `tick` (docs/09, section 9)."""
        return self.model_copy(update={"last_conversation_end_tick": tick})

    def hail_cooldown_left(self, tick: int, cooldown_ticks: int) -> int:
        """Ticks before this entity may be hailed again; 0 when it may be now."""
        if self.last_conversation_end_tick < 0:
            return 0
        elapsed = tick - self.last_conversation_end_tick
        return max(0, cooldown_ticks - elapsed)

    def as_dead(self) -> "Entity":
        """Return copy marked dead: no health, no inventory, nothing wielded."""
        return self.model_copy(
            update={
                "alive": False,
                "health": 0,
                "inventory": Inventory(),
                "wielded": "",
                "status_bits": self.status_bits | STATUS_BIT_DEAD,
                "asleep": False,
                "sleeping_on": "",
                "collapsed": False,
            }
        )

    def as_respawned(self, position: Position, food: int, fatigue: int = 0) -> "Entity":
        """Return a fresh copy for respawn at `position`."""
        return self.model_copy(
            update={
                "position": position,
                "alive": True,
                "health": self.max_health,
                "food": max(0, min(self.max_food, food)),
                "fatigue": max(0, min(self.max_fatigue, fatigue)),
                "asleep": False,
                "sleeping_on": "",
                "collapsed": False,
                "inventory": Inventory(),
                "wielded": "",
                "status_bits": self.status_bits & ~STATUS_BIT_DEAD,
            }
        )


class WorldObject(BaseModel, frozen=True):
    """Immutable world object state (bushes, chests, etc.)."""

    object_id: str
    position: Position
    object_type: str
    state: tuple[tuple[str, str], ...] = ()

    def get_state(self, key: str, default: str = "") -> str:
        """Get state value by key."""
        for k, v in self.state:
            if k == key:
                return v
        return default

    def with_state(self, key: str, value: str) -> "WorldObject":
        """Return copy with updated state value."""
        new_state = dict(self.state)
        new_state[key] = value
        return self.model_copy(update={"state": tuple(new_state.items())})
