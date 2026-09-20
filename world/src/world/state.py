"""World state management."""

from typing import TYPE_CHECKING, Mapping

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, PrivateAttr

if TYPE_CHECKING:
    from .terrain_types import FloorType


# Floor value -> (walkable, opaque, floor_type_str)
# Matches terrain/classification.py _floor_value mapping
_FLOOR_VALUE_PROPERTIES: dict[int, tuple[bool, bool, str]] = {
    0: (False, False, "deep_water"),  # DEEP_WATER - not walkable
    1: (True, False, "shallow_water"),  # SHALLOW_WATER - walkable
    2: (True, False, "sand"),  # SAND
    3: (True, False, "grass"),  # GRASS
    4: (True, False, "dirt"),  # DIRT
    5: (False, True, "mountain"),  # MOUNTAIN - not walkable, opaque
    6: (True, False, "stone"),  # STONE (default)
}

from .exceptions import (
    EntityAlreadyExistsError,
    EntityNotFoundError,
    ObjectAlreadyExistsError,
    ObjectNotFoundError,
    PositionOccupiedError,
)
from .items import BLOCKING_OBJECT_TYPES, WOLF_BLOCKING_OBJECT_TYPES
from .types import Position

# status_bits flag for a dead entity (bit 0), see docs/05_jev_agents_design.md.
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
    """Where the world sits in its day/night cycle."""

    day: int
    tick_of_day: int
    day_length: int
    night: bool


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
    def from_floor_type(cls, position: Position, floor_type: "FloorType") -> "Tile":
        """Create a tile with properties derived from floor type.

        Args:
            position: Tile position.
            floor_type: FloorType enum value.

        Returns:
            Tile with walkable/opaque properties set based on floor type.
        """
        from .terrain_types import FloorType as FT

        if not isinstance(floor_type, FT):
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


class World(BaseModel):
    """
    Mutable world state container.

    Uses frozen models internally but allows replacing them.
    Grid is sparse: only non-default tiles are stored.
    Floor array (if set) provides efficient bulk terrain storage.
    """

    width: int
    height: int
    tick: int = 0
    # Ticks in one day/night cycle (docs/10_metal_and_sleep.md, "The day").
    day_length_ticks: int = DEFAULT_DAY_LENGTH_TICKS
    # Settlement centre (set by settlement.py when spawn_mode = "settlement").
    settlement: Position | None = None

    # Private attributes for internal state
    # Optional floor array for efficient terrain storage (from terrain generation)
    # Shape: (height, width), dtype: uint8, values match _FLOOR_VALUE_PROPERTIES keys
    _floor_array: NDArray[np.uint8] | None = PrivateAttr(default=None)

    # Sparse tile storage: only tiles that differ from floor_array (or all tiles if no array)
    _tiles: dict[Position, Tile] = PrivateAttr(default_factory=dict)

    # Entity registry
    _entities: dict[str, Entity] = PrivateAttr(default_factory=dict)

    # Position index for quick lookups
    _entity_positions: dict[Position, str] = PrivateAttr(default_factory=dict)

    # Object registry (multiple objects can share a position)
    _objects: dict[str, WorldObject] = PrivateAttr(default_factory=dict)
    _object_positions: dict[Position, list[str]] = PrivateAttr(default_factory=dict)

    # entity_id -> tick at which the entity died (drives respawn scheduling)
    _death_ticks: dict[str, int] = PrivateAttr(default_factory=dict)

    # Tiles holding a blocking object -> how many such objects stand there.
    # Maintained by add_object/remove_object/update_object; walls block
    # everyone, doors block wolves only (docs/08_building.md).
    _blocked_positions: dict[Position, int] = PrivateAttr(default_factory=dict)
    _wolf_blocked_positions: dict[Position, int] = PrivateAttr(default_factory=dict)

    # Monotonic counter backing generate_object_id()
    _object_id_seq: int = PrivateAttr(default=0)

    # --- Tile operations ---

    def set_floor_array(self, floor_array: NDArray[np.uint8]) -> None:
        """Set the floor array for efficient terrain storage.

        Args:
            floor_array: 2D array of floor type values, shape (height, width).
                        Values must match _FLOOR_VALUE_PROPERTIES keys.
        """
        if floor_array.shape != (self.height, self.width):
            raise ValueError(
                f"Floor array shape {floor_array.shape} doesn't match "
                f"world dimensions ({self.height}, {self.width})"
            )
        self._floor_array = floor_array

    def get_tile(self, position: Position) -> Tile:
        """Get tile at position.

        Priority: sparse _tiles dict > floor_array > default tile.
        """
        if not self.in_bounds(position):
            return Tile(position=position, walkable=False, opaque=True)

        # Check sparse overrides first
        if position in self._tiles:
            return self._tiles[position]

        # Check floor array if available
        if self._floor_array is not None:
            floor_value = int(self._floor_array[position.y, position.x])
            walkable, opaque, floor_type = _FLOOR_VALUE_PROPERTIES.get(
                floor_value, (True, False, "stone")
            )
            return Tile(
                position=position,
                walkable=walkable,
                opaque=opaque,
                floor_type=floor_type,
            )

        # Default tile
        return Tile(position=position)

    def set_tile(self, tile: Tile) -> None:
        """Set tile properties (stored in sparse dict, overrides floor_array)."""
        self._tiles[tile.position] = tile

    def is_walkable(self, position: Position) -> bool:
        """Check if position is walkable.

        Optimized to avoid creating Tile objects when using floor_array.
        """
        if not self.in_bounds(position):
            return False

        # Check sparse overrides first
        if position in self._tiles:
            return self._tiles[position].walkable

        # Check floor array if available
        if self._floor_array is not None:
            floor_value = int(self._floor_array[position.y, position.x])
            walkable, _, _ = _FLOOR_VALUE_PROPERTIES.get(
                floor_value, (True, False, "stone")
            )
            return walkable

        # Default is walkable
        return True

    def in_bounds(self, position: Position) -> bool:
        """Check if position is within world bounds."""
        return 0 <= position.x < self.width and 0 <= position.y < self.height

    def is_blocked(
        self, position: Position, entity_type: str = DEFAULT_ENTITY_TYPE
    ) -> bool:
        """Whether an object on this tile stops an entity of this type."""
        if position in self._blocked_positions:
            return True
        if entity_type == WOLF_ENTITY_TYPE:
            return position in self._wolf_blocked_positions
        return False

    def is_passable(
        self, position: Position, entity_type: str = DEFAULT_ENTITY_TYPE
    ) -> bool:
        """Walkable terrain in bounds with no blocking object for this type.

        Says nothing about entities standing there; movement resolution and
        `settlement.is_free_walkable` add that rule.
        """
        return self.is_walkable(position) and not self.is_blocked(position, entity_type)

    # --- Entity operations ---

    def add_entity(self, entity: Entity) -> None:
        """Add entity to world.

        Raises:
            EntityAlreadyExistsError: If entity with same ID already exists.
            PositionOccupiedError: If position is already occupied.
        """
        if entity.entity_id in self._entities:
            raise EntityAlreadyExistsError(f"Entity {entity.entity_id} already exists")
        if entity.position in self._entity_positions:
            occupant = self._entity_positions[entity.position]
            raise PositionOccupiedError(
                f"Position {entity.position} already occupied by {occupant}"
            )
        self._entities[entity.entity_id] = entity
        self._entity_positions[entity.position] = entity.entity_id

    def get_entity(self, entity_id: str) -> Entity:
        """Get entity by ID.

        Raises:
            EntityNotFoundError: If entity not found.
        """
        if entity_id not in self._entities:
            raise EntityNotFoundError(f"Entity {entity_id} not found")
        return self._entities[entity_id]

    def get_entity_at(self, position: Position) -> Entity | None:
        """Get entity at position, or None."""
        entity_id = self._entity_positions.get(position)
        return self._entities.get(entity_id) if entity_id else None

    def remove_entity(self, entity_id: str) -> Entity:
        """Remove and return entity.

        Raises:
            EntityNotFoundError: If entity not found.
        """
        if entity_id not in self._entities:
            raise EntityNotFoundError(f"Entity {entity_id} not found")
        entity = self._entities.pop(entity_id)
        del self._entity_positions[entity.position]
        return entity

    def update_entity_position(self, entity_id: str, new_position: Position) -> None:
        """Update entity position atomically.

        Raises:
            EntityNotFoundError: If entity not found.
        """
        if entity_id not in self._entities:
            raise EntityNotFoundError(f"Entity {entity_id} not found")

        entity = self._entities[entity_id]
        old_position = entity.position

        # Update indices
        del self._entity_positions[old_position]
        self._entity_positions[new_position] = entity_id

        # Update entity
        self._entities[entity_id] = entity.with_position(new_position)

    def all_entities(self) -> Mapping[str, Entity]:
        """Return read-only view of all entities."""
        return self._entities

    def entity_count(self) -> int:
        """Return number of entities in the world."""
        return len(self._entities)

    def is_position_occupied(self, position: Position) -> bool:
        """Check if position has an entity."""
        return position in self._entity_positions

    def set_entity(self, entity: Entity) -> None:
        """Replace an entity whose position has not changed.

        Raises:
            EntityNotFoundError: If entity not found.
            ValueError: If the entity's position differs from the indexed one.
        """
        existing = self._entities.get(entity.entity_id)
        if existing is None:
            raise EntityNotFoundError(f"Entity {entity.entity_id} not found")
        if existing.position != entity.position:
            raise ValueError(
                f"set_entity() cannot move {entity.entity_id}; "
                "use update_entity_position()"
            )
        self._entities[entity.entity_id] = entity

    def detach_entity(self, entity_id: str) -> None:
        """Remove an entity from the position index while keeping the record.

        Used for dead entities, which stay in all_entities() but no longer
        occupy a tile.

        Raises:
            EntityNotFoundError: If entity not found or not currently placed.
        """
        entity = self._entities.get(entity_id)
        if entity is None:
            raise EntityNotFoundError(f"Entity {entity_id} not found")
        if self._entity_positions.get(entity.position) != entity_id:
            raise EntityNotFoundError(
                f"Entity {entity_id} is not present in the position index"
            )
        del self._entity_positions[entity.position]

    def attach_entity(self, entity_id: str, position: Position) -> None:
        """Place a detached entity back onto the grid at `position`.

        Raises:
            EntityNotFoundError: If entity not found.
            PositionOccupiedError: If the tile already holds an entity.
        """
        entity = self._entities.get(entity_id)
        if entity is None:
            raise EntityNotFoundError(f"Entity {entity_id} not found")
        if position in self._entity_positions:
            occupant = self._entity_positions[position]
            raise PositionOccupiedError(
                f"Position {position} already occupied by {occupant}"
            )
        self._entity_positions[position] = entity_id
        self._entities[entity_id] = entity.with_position(position)

    def discard_entity(self, entity_id: str) -> None:
        """Delete an entity record that is no longer on the grid.

        Raises:
            EntityNotFoundError: If entity not found.
            ValueError: If the entity is still in the position index.
        """
        entity = self._entities.get(entity_id)
        if entity is None:
            raise EntityNotFoundError(f"Entity {entity_id} not found")
        if self._entity_positions.get(entity.position) == entity_id:
            raise ValueError(
                f"Entity {entity_id} is still placed; detach_entity() first"
            )
        del self._entities[entity_id]

    def living_entities(self) -> list[Entity]:
        """All entities currently alive."""
        return [e for e in self._entities.values() if e.alive]

    # --- Death bookkeeping ---

    def mark_death(self, entity_id: str, tick: int) -> None:
        """Record the tick at which `entity_id` died."""
        self._death_ticks[entity_id] = tick

    def death_tick(self, entity_id: str) -> int:
        """Tick at which the entity died.

        Raises:
            KeyError: If the entity has no recorded death.
        """
        return self._death_ticks[entity_id]

    def pending_respawns(self) -> Mapping[str, int]:
        """Read-only view of entity_id -> death tick for dead entities."""
        return self._death_ticks

    def clear_death(self, entity_id: str) -> None:
        """Forget a recorded death (after respawn or despawn)."""
        self._death_ticks.pop(entity_id, None)

    # --- Object operations ---

    def add_object(self, obj: WorldObject) -> None:
        """Add object to world.

        Raises:
            ObjectAlreadyExistsError: If object with same ID already exists.
        """
        if obj.object_id in self._objects:
            raise ObjectAlreadyExistsError(f"Object {obj.object_id} already exists")
        self._objects[obj.object_id] = obj
        if obj.position not in self._object_positions:
            self._object_positions[obj.position] = []
        self._object_positions[obj.position].append(obj.object_id)
        self._index_blocking(obj, 1)

    def get_object(self, object_id: str) -> WorldObject:
        """Get object by ID.

        Raises:
            ObjectNotFoundError: If object not found.
        """
        if object_id not in self._objects:
            raise ObjectNotFoundError(f"Object {object_id} not found")
        return self._objects[object_id]

    def get_objects_at(self, position: Position) -> list[WorldObject]:
        """Get all objects at position."""
        object_ids = self._object_positions.get(position, [])
        return [self._objects[oid] for oid in object_ids]

    def update_object(self, obj: WorldObject) -> None:
        """Update object state (must already exist, position unchanged).

        Raises:
            ObjectNotFoundError: If object not found.
        """
        existing = self._objects.get(obj.object_id)
        if existing is None:
            raise ObjectNotFoundError(f"Object {obj.object_id} not found")
        # Type and position are stable in practice; re-index defensively so the
        # blocking index can never drift from the object registry.
        if (existing.object_type, existing.position) != (obj.object_type, obj.position):
            self._index_blocking(existing, -1)
            self._index_blocking(obj, 1)
        self._objects[obj.object_id] = obj

    def remove_object(self, object_id: str) -> WorldObject:
        """Remove and return an object.

        Raises:
            ObjectNotFoundError: If object not found.
        """
        if object_id not in self._objects:
            raise ObjectNotFoundError(f"Object {object_id} not found")
        obj = self._objects.pop(object_id)
        ids_at = self._object_positions.get(obj.position, [])
        if object_id in ids_at:
            ids_at.remove(object_id)
        if not ids_at:
            self._object_positions.pop(obj.position, None)
        self._index_blocking(obj, -1)
        return obj

    def _index_blocking(self, obj: WorldObject, delta: int) -> None:
        """Add (delta=1) or drop (delta=-1) one object from the blocking index."""
        for index, blocking_types in (
            (self._blocked_positions, BLOCKING_OBJECT_TYPES),
            (self._wolf_blocked_positions, WOLF_BLOCKING_OBJECT_TYPES),
        ):
            if obj.object_type not in blocking_types:
                continue
            count = index.get(obj.position, 0) + delta
            if count > 0:
                index[obj.position] = count
            else:
                index.pop(obj.position, None)

    def generate_object_id(self, prefix: str) -> str:
        """Return an unused object id of the form `<prefix>_<n>`."""
        while True:
            self._object_id_seq += 1
            candidate = f"{prefix}_{self._object_id_seq}"
            if candidate not in self._objects:
                return candidate

    def all_objects(self) -> Mapping[str, WorldObject]:
        """Return read-only view of all objects."""
        return self._objects

    def object_count(self) -> int:
        """Return number of objects in the world."""
        return len(self._objects)

    # --- Chunk operations ---

    def get_terrain_chunk(
        self, chunk_x: int, chunk_y: int, chunk_size: int = 32
    ) -> NDArray[np.uint8]:
        """Extract terrain data for a chunk region.

        Returns a chunk_size x chunk_size array of floor values.
        Out-of-bounds areas are filled with 0 (deep water).
        Sparse tile overrides are applied on top of floor_array data.

        Args:
            chunk_x: Chunk x coordinate.
            chunk_y: Chunk y coordinate.
            chunk_size: Size of chunk (default 32).

        Returns:
            2D uint8 array of floor values, shape (chunk_size, chunk_size).
        """
        x_start = chunk_x * chunk_size
        y_start = chunk_y * chunk_size

        # Initialize chunk with default stone (6)
        chunk = np.full((chunk_size, chunk_size), 6, dtype=np.uint8)

        if self._floor_array is not None:
            # Calculate valid region within world bounds
            x_end = min(x_start + chunk_size, self.width)
            y_end = min(y_start + chunk_size, self.height)

            # Only copy if there's valid overlap
            if x_start < self.width and y_start < self.height:
                valid_w = max(0, x_end - x_start)
                valid_h = max(0, y_end - y_start)

                if valid_w > 0 and valid_h > 0:
                    chunk[:valid_h, :valid_w] = self._floor_array[
                        y_start:y_end, x_start:x_end
                    ]

        # Apply sparse tile overrides
        for pos, tile in self._tiles.items():
            local_x = pos.x - x_start
            local_y = pos.y - y_start
            if 0 <= local_x < chunk_size and 0 <= local_y < chunk_size:
                # Map floor_type string back to numeric value
                floor_type_to_value = {
                    "deep_water": 0,
                    "shallow_water": 1,
                    "sand": 2,
                    "grass": 3,
                    "dirt": 4,
                    "mountain": 5,
                    "stone": 6,
                }
                chunk[local_y, local_x] = floor_type_to_value.get(tile.floor_type, 6)

        return chunk

    # --- Tick operations ---

    @property
    def clock(self) -> WorldClock:
        """Where the current tick sits in the day (docs/10)."""
        length = self.day_length_ticks
        tick_of_day = self.tick % length
        return WorldClock(
            day=self.tick // length,
            tick_of_day=tick_of_day,
            day_length=length,
            night=tick_of_day >= night_start_tick(length),
        )

    def advance_tick(self) -> None:
        """Increment tick counter."""
        self.tick += 1
