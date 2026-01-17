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
from .types import Position


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
            raise ValueError(
                f"Cannot remove {amount} {item_type}, only have {current}"
            )
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
    def from_floor_type(
        cls, position: Position, floor_type: "FloorType"
    ) -> "Tile":
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

    def with_position(self, new_position: Position) -> "Entity":
        """Return copy with updated position."""
        return self.model_copy(update={"position": new_position})

    def with_inventory(self, new_inventory: Inventory) -> "Entity":
        """Return copy with updated inventory."""
        return self.model_copy(update={"inventory": new_inventory})


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
        if obj.object_id not in self._objects:
            raise ObjectNotFoundError(f"Object {obj.object_id} not found")
        self._objects[obj.object_id] = obj

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
                chunk[local_y, local_x] = floor_type_to_value.get(
                    tile.floor_type, 6
                )

        return chunk

    # --- Tick operations ---

    def advance_tick(self) -> None:
        """Increment tick counter."""
        self.tick += 1
