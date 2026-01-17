"""Chunk-based spatial indexing for large world support."""

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from .state import World
from .types import Position

CHUNK_SIZE = 32


def chunk_coords(x: int, y: int) -> tuple[int, int]:
    """Convert world coordinates to chunk coordinates."""
    return (x // CHUNK_SIZE, y // CHUNK_SIZE)


def world_coords(chunk_x: int, chunk_y: int, local_x: int, local_y: int) -> tuple[int, int]:
    """Convert chunk + local offset to world coordinates."""
    return (chunk_x * CHUNK_SIZE + local_x, chunk_y * CHUNK_SIZE + local_y)


def local_coords(x: int, y: int) -> tuple[int, int]:
    """Convert world coordinates to local coordinates within a chunk."""
    return (x % CHUNK_SIZE, y % CHUNK_SIZE)


@dataclass
class Chunk:
    """A CHUNK_SIZE x CHUNK_SIZE region of the world."""

    chunk_x: int
    chunk_y: int
    terrain: NDArray[np.uint8]  # Shape: (CHUNK_SIZE, CHUNK_SIZE)
    entities: set[str] = field(default_factory=set)
    objects: set[str] = field(default_factory=set)
    version: int = 0

    def increment_version(self) -> None:
        """Increment version number (call on any change)."""
        self.version += 1


class ChunkManager:
    """Manages chunk indexing for the world.

    Provides O(1) lookup of which chunk contains an entity or object,
    and efficient extraction of entities/objects within a viewport.
    """

    def __init__(self, world: World):
        self.world = world
        self._chunks: dict[tuple[int, int], Chunk] = {}
        self._entity_chunks: dict[str, tuple[int, int]] = {}
        self._object_chunks: dict[str, tuple[int, int]] = {}

    @property
    def chunk_count_x(self) -> int:
        """Number of chunks along x axis."""
        return (self.world.width + CHUNK_SIZE - 1) // CHUNK_SIZE

    @property
    def chunk_count_y(self) -> int:
        """Number of chunks along y axis."""
        return (self.world.height + CHUNK_SIZE - 1) // CHUNK_SIZE

    def initialize_from_world(self) -> None:
        """Build chunk index from existing world state.

        Call this after world is loaded/generated to populate chunk indices.
        """
        # Index existing entities
        for entity_id, entity in self.world.all_entities().items():
            cx, cy = chunk_coords(entity.position.x, entity.position.y)
            self._entity_chunks[entity_id] = (cx, cy)
            chunk = self._get_or_create_chunk(cx, cy)
            chunk.entities.add(entity_id)

        # Index existing objects
        for object_id, obj in self.world.all_objects().items():
            cx, cy = chunk_coords(obj.position.x, obj.position.y)
            self._object_chunks[object_id] = (cx, cy)
            chunk = self._get_or_create_chunk(cx, cy)
            chunk.objects.add(object_id)

    def _get_or_create_chunk(self, chunk_x: int, chunk_y: int) -> Chunk:
        """Get existing chunk or create a new one with terrain data."""
        key = (chunk_x, chunk_y)
        if key not in self._chunks:
            terrain = self.world.get_terrain_chunk(chunk_x, chunk_y)
            self._chunks[key] = Chunk(
                chunk_x=chunk_x,
                chunk_y=chunk_y,
                terrain=terrain,
            )
        return self._chunks[key]

    def get_chunk(self, chunk_x: int, chunk_y: int) -> Chunk | None:
        """Get chunk at coordinates, or None if out of bounds.

        Creates the chunk lazily if it doesn't exist yet.
        """
        if chunk_x < 0 or chunk_y < 0:
            return None
        if chunk_x >= self.chunk_count_x or chunk_y >= self.chunk_count_y:
            return None
        return self._get_or_create_chunk(chunk_x, chunk_y)

    def get_chunks_for_viewport(
        self, x: int, y: int, width: int, height: int, padding: int = 1
    ) -> list[tuple[int, int]]:
        """Return chunk coordinates that overlap a viewport rectangle.

        Args:
            x, y: Top-left corner of viewport in world coordinates.
            width, height: Size of viewport in tiles.
            padding: Extra chunks to include beyond viewport edges.

        Returns:
            List of (chunk_x, chunk_y) tuples.
        """
        start_cx = max(0, x // CHUNK_SIZE - padding)
        start_cy = max(0, y // CHUNK_SIZE - padding)
        end_cx = min(self.chunk_count_x, (x + width - 1) // CHUNK_SIZE + 1 + padding)
        end_cy = min(self.chunk_count_y, (y + height - 1) // CHUNK_SIZE + 1 + padding)

        chunks = []
        for cy in range(start_cy, end_cy):
            for cx in range(start_cx, end_cx):
                chunks.append((cx, cy))
        return chunks

    def update_entity_position(
        self, entity_id: str, old_pos: Position, new_pos: Position
    ) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
        """Update entity's chunk membership on movement.

        Returns:
            Tuple of (old_chunk_coords, new_chunk_coords).
            Either may be None if entity wasn't tracked or is out of bounds.
        """
        old_chunk = chunk_coords(old_pos.x, old_pos.y)
        new_chunk = chunk_coords(new_pos.x, new_pos.y)

        # Remove from old chunk
        old_chunk_obj = self._chunks.get(old_chunk)
        if old_chunk_obj and entity_id in old_chunk_obj.entities:
            old_chunk_obj.entities.discard(entity_id)
            old_chunk_obj.increment_version()

        # Add to new chunk
        new_chunk_obj = self._get_or_create_chunk(new_chunk[0], new_chunk[1])
        new_chunk_obj.entities.add(entity_id)
        new_chunk_obj.increment_version()

        # Update index
        self._entity_chunks[entity_id] = new_chunk

        if old_chunk != new_chunk:
            return (old_chunk, new_chunk)
        return (None, None)

    def add_entity(self, entity_id: str, position: Position) -> tuple[int, int]:
        """Register a new entity in chunk tracking.

        Returns:
            Chunk coordinates where entity was added.
        """
        cx, cy = chunk_coords(position.x, position.y)
        self._entity_chunks[entity_id] = (cx, cy)
        chunk = self._get_or_create_chunk(cx, cy)
        chunk.entities.add(entity_id)
        chunk.increment_version()
        return (cx, cy)

    def remove_entity(self, entity_id: str) -> tuple[int, int] | None:
        """Remove entity from chunk tracking.

        Returns:
            Chunk coordinates where entity was, or None if not tracked.
        """
        chunk_key = self._entity_chunks.pop(entity_id, None)
        if chunk_key:
            chunk = self._chunks.get(chunk_key)
            if chunk:
                chunk.entities.discard(entity_id)
                chunk.increment_version()
        return chunk_key

    def add_object(self, object_id: str, position: Position) -> tuple[int, int]:
        """Register a new object in chunk tracking.

        Returns:
            Chunk coordinates where object was added.
        """
        cx, cy = chunk_coords(position.x, position.y)
        self._object_chunks[object_id] = (cx, cy)
        chunk = self._get_or_create_chunk(cx, cy)
        chunk.objects.add(object_id)
        chunk.increment_version()
        return (cx, cy)

    def remove_object(self, object_id: str) -> tuple[int, int] | None:
        """Remove object from chunk tracking.

        Returns:
            Chunk coordinates where object was, or None if not tracked.
        """
        chunk_key = self._object_chunks.pop(object_id, None)
        if chunk_key:
            chunk = self._chunks.get(chunk_key)
            if chunk:
                chunk.objects.discard(object_id)
                chunk.increment_version()
        return chunk_key

    def get_entity_chunk(self, entity_id: str) -> tuple[int, int] | None:
        """Get chunk coordinates for an entity."""
        return self._entity_chunks.get(entity_id)

    def get_object_chunk(self, object_id: str) -> tuple[int, int] | None:
        """Get chunk coordinates for an object."""
        return self._object_chunks.get(object_id)

    def get_entities_in_chunks(
        self, chunk_coords_list: list[tuple[int, int]]
    ) -> set[str]:
        """Get all entity IDs in the specified chunks."""
        entities: set[str] = set()
        for cx, cy in chunk_coords_list:
            chunk = self._chunks.get((cx, cy))
            if chunk:
                entities.update(chunk.entities)
        return entities

    def get_objects_in_chunks(
        self, chunk_coords_list: list[tuple[int, int]]
    ) -> set[str]:
        """Get all object IDs in the specified chunks."""
        objects: set[str] = set()
        for cx, cy in chunk_coords_list:
            chunk = self._chunks.get((cx, cy))
            if chunk:
                objects.update(chunk.objects)
        return objects
