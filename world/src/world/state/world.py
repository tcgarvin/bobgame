"""`World`: the one mutable container, holding tiles, entities and objects.

The models it holds are frozen (`models.py`); `World` replaces them. Two
invariants matter and are documented on the methods that keep them: the entity
id and position indexes must move together (`update_entity_position`), and the
per-tile blocking counts must follow every object add, remove and update.
"""

from typing import Mapping

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, PrivateAttr

from ..exceptions import (
    EntityAlreadyExistsError,
    EntityNotFoundError,
    ObjectAlreadyExistsError,
    ObjectNotFoundError,
    PositionOccupiedError,
)
from ..items import BLOCKING_OBJECT_TYPES, WOLF_BLOCKING_OBJECT_TYPES
from ..terrain_types import DEFAULT_FLOOR_TYPE, FLOOR_TYPE_BY_CODE
from ..types import Position
from .models import (
    DEFAULT_DAY_LENGTH_TICKS,
    DEFAULT_ENTITY_TYPE,
    WOLF_ENTITY_TYPE,
    Entity,
    Tile,
    WorldClock,
    WorldObject,
    night_start_tick,
)

# Floor code -> (walkable, opaque, floor_type string), from the one table in
# `terrain_types.py`.
_FLOOR_VALUE_PROPERTIES: Mapping[int, tuple[bool, bool, str]] = {
    code: (floor_type.walkable, floor_type.opaque, floor_type.value)
    for code, floor_type in FLOOR_TYPE_BY_CODE.items()
}

# What a floor code outside the table reads as.
_DEFAULT_FLOOR_PROPERTIES = (
    DEFAULT_FLOOR_TYPE.walkable,
    DEFAULT_FLOOR_TYPE.opaque,
    DEFAULT_FLOOR_TYPE.value,
)


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
    # How often a new-moon night falls, in days; 0 means never (docs/14).
    new_moon_every_days: int = 0
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

    @property
    def floor_array(self) -> NDArray[np.uint8] | None:
        """The bulk terrain array, or None for a world built tile by tile.

        Read-only by convention: callers index it, they do not write it. Use
        `set_floor_array` to replace it.
        """
        return self._floor_array

    def tile_overrides(self) -> Mapping[Position, Tile]:
        """Read-only view of the sparse tiles that override `floor_array`."""
        return self._tiles

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
                floor_value, _DEFAULT_FLOOR_PROPERTIES
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
                floor_value, _DEFAULT_FLOOR_PROPERTIES
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

    def add_entity_unplaced(self, entity: Entity) -> None:
        """Add an entity record without putting it in the position index.

        Dead entities live in the registry but occupy no tile (see
        `detach_entity`); restoring a snapshot puts them back this way, since
        two of them may share the coordinates they died on.

        Raises:
            EntityAlreadyExistsError: If an entity with that id exists.
        """
        if entity.entity_id in self._entities:
            raise EntityAlreadyExistsError(f"Entity {entity.entity_id} already exists")
        self._entities[entity.entity_id] = entity

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

    @property
    def object_id_seq(self) -> int:
        """The counter behind `generate_object_id`, saved with a snapshot."""
        return self._object_id_seq

    def set_object_id_seq(self, value: int) -> None:
        """Restore the object id counter from a snapshot (docs/14).

        Raises:
            ValueError: If `value` is negative.
        """
        if value < 0:
            raise ValueError(f"object_id_seq must not be negative, got {value}")
        self._object_id_seq = value

    def all_objects(self) -> Mapping[str, WorldObject]:
        """Return read-only view of all objects."""
        return self._objects

    def object_count(self) -> int:
        """Return number of objects in the world."""
        return len(self._objects)

    # --- Tick operations ---

    @property
    def clock(self) -> WorldClock:
        """Where the current tick sits in the day (docs/10) and the moon (docs/14).

        `moon` is imported here rather than at module level because it reaches
        back into this module for `World`; the import is a `sys.modules` lookup
        and the clock is built a handful of times per tick.
        """
        from ..moon import is_new_moon_day, next_new_moon_day

        length = self.day_length_ticks
        tick_of_day = self.tick % length
        day = self.tick // length
        every = self.new_moon_every_days
        return WorldClock(
            day=day,
            tick_of_day=tick_of_day,
            day_length=length,
            night=tick_of_day >= night_start_tick(length),
            new_moon_tonight=is_new_moon_day(day, every),
            next_new_moon_day=next_new_moon_day(day, every),
        )

    def advance_tick(self) -> None:
        """Increment tick counter."""
        self.tick += 1
