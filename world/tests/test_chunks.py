"""Tests for chunk-based spatial indexing."""

import numpy as np
import pytest

from world.chunks import (
    CHUNK_SIZE,
    Chunk,
    ChunkManager,
    chunk_coords,
    local_coords,
    world_coords,
)
from world.state import Entity, World, WorldObject
from world.types import Position


class TestCoordinateConversion:
    """Tests for coordinate conversion functions."""

    def test_chunk_coords_origin(self) -> None:
        """Position (0,0) is in chunk (0,0)."""
        assert chunk_coords(0, 0) == (0, 0)

    def test_chunk_coords_within_first_chunk(self) -> None:
        """Positions 0-31 are in chunk 0."""
        assert chunk_coords(15, 20) == (0, 0)
        assert chunk_coords(31, 31) == (0, 0)

    def test_chunk_coords_second_chunk(self) -> None:
        """Position 32 is in chunk 1."""
        assert chunk_coords(32, 0) == (1, 0)
        assert chunk_coords(0, 32) == (0, 1)
        assert chunk_coords(32, 32) == (1, 1)

    def test_chunk_coords_large_position(self) -> None:
        """Large positions map to correct chunks."""
        assert chunk_coords(100, 200) == (3, 6)
        assert chunk_coords(2000, 3000) == (62, 93)

    def test_world_coords_roundtrip(self) -> None:
        """world_coords reverses chunk_coords + local_coords."""
        for x, y in [(0, 0), (50, 75), (1000, 2000)]:
            cx, cy = chunk_coords(x, y)
            lx, ly = local_coords(x, y)
            wx, wy = world_coords(cx, cy, lx, ly)
            assert (wx, wy) == (x, y)

    def test_local_coords(self) -> None:
        """Local coords are position within chunk."""
        assert local_coords(0, 0) == (0, 0)
        assert local_coords(31, 31) == (31, 31)
        assert local_coords(32, 33) == (0, 1)
        assert local_coords(50, 75) == (18, 11)


class TestChunk:
    """Tests for Chunk dataclass."""

    def test_chunk_creation(self) -> None:
        """Chunk can be created with terrain data."""
        terrain = np.full((CHUNK_SIZE, CHUNK_SIZE), 3, dtype=np.uint8)
        chunk = Chunk(chunk_x=0, chunk_y=0, terrain=terrain)

        assert chunk.chunk_x == 0
        assert chunk.chunk_y == 0
        assert chunk.terrain.shape == (CHUNK_SIZE, CHUNK_SIZE)
        assert len(chunk.entities) == 0
        assert len(chunk.objects) == 0
        assert chunk.version == 0

    def test_chunk_version_increment(self) -> None:
        """Version increments on changes."""
        terrain = np.zeros((CHUNK_SIZE, CHUNK_SIZE), dtype=np.uint8)
        chunk = Chunk(chunk_x=0, chunk_y=0, terrain=terrain)

        assert chunk.version == 0
        chunk.increment_version()
        assert chunk.version == 1
        chunk.increment_version()
        assert chunk.version == 2


class TestChunkManager:
    """Tests for ChunkManager."""

    @pytest.fixture
    def large_world(self) -> World:
        """Create a 100x100 world with floor array."""
        world = World(width=100, height=100)
        floor_array = np.full((100, 100), 3, dtype=np.uint8)  # All grass
        world.set_floor_array(floor_array)
        return world

    @pytest.fixture
    def manager(self, large_world: World) -> ChunkManager:
        """Create ChunkManager for large world."""
        return ChunkManager(large_world)

    def test_chunk_count(self, manager: ChunkManager) -> None:
        """100x100 world has 4x4 chunks."""
        assert manager.chunk_count_x == 4  # ceil(100/32)
        assert manager.chunk_count_y == 4

    def test_get_chunk_creates_lazily(self, manager: ChunkManager) -> None:
        """Chunks are created on first access."""
        assert len(manager._chunks) == 0

        chunk = manager.get_chunk(0, 0)
        assert chunk is not None
        assert len(manager._chunks) == 1
        assert chunk.terrain.shape == (CHUNK_SIZE, CHUNK_SIZE)

    def test_get_chunk_out_of_bounds(self, manager: ChunkManager) -> None:
        """Out of bounds chunks return None."""
        assert manager.get_chunk(-1, 0) is None
        assert manager.get_chunk(0, -1) is None
        assert manager.get_chunk(10, 0) is None  # > 4 chunks
        assert manager.get_chunk(0, 10) is None

    def test_get_chunks_for_viewport(self, manager: ChunkManager) -> None:
        """Viewport returns overlapping chunks."""
        # Viewport at origin covering first chunk
        chunks = manager.get_chunks_for_viewport(0, 0, 32, 32, padding=0)
        assert (0, 0) in chunks

        # Viewport spanning multiple chunks
        chunks = manager.get_chunks_for_viewport(16, 16, 48, 48, padding=0)
        assert (0, 0) in chunks
        assert (1, 0) in chunks
        assert (0, 1) in chunks
        assert (1, 1) in chunks

    def test_get_chunks_for_viewport_with_padding(self, manager: ChunkManager) -> None:
        """Padding adds extra chunks around viewport."""
        chunks = manager.get_chunks_for_viewport(32, 32, 32, 32, padding=1)
        # Viewport covers chunk (1,1), padding adds neighbors
        assert (0, 0) in chunks
        assert (1, 0) in chunks
        assert (2, 0) in chunks
        assert (0, 1) in chunks
        assert (1, 1) in chunks
        assert (2, 1) in chunks
        assert (0, 2) in chunks
        assert (1, 2) in chunks
        assert (2, 2) in chunks

    def test_initialize_from_world_indexes_entities(
        self, large_world: World
    ) -> None:
        """Entities are indexed into chunks."""
        large_world.add_entity(
            Entity(entity_id="alice", position=Position(x=10, y=10))
        )
        large_world.add_entity(
            Entity(entity_id="bob", position=Position(x=50, y=60))
        )

        manager = ChunkManager(large_world)
        manager.initialize_from_world()

        assert manager.get_entity_chunk("alice") == (0, 0)
        assert manager.get_entity_chunk("bob") == (1, 1)

        chunk_0_0 = manager.get_chunk(0, 0)
        assert chunk_0_0 is not None
        assert "alice" in chunk_0_0.entities

    def test_initialize_from_world_indexes_objects(
        self, large_world: World
    ) -> None:
        """Objects are indexed into chunks."""
        large_world.add_object(
            WorldObject(
                object_id="bush1",
                position=Position(x=5, y=5),
                object_type="bush",
            )
        )
        large_world.add_object(
            WorldObject(
                object_id="bush2",
                position=Position(x=70, y=80),
                object_type="bush",
            )
        )

        manager = ChunkManager(large_world)
        manager.initialize_from_world()

        assert manager.get_object_chunk("bush1") == (0, 0)
        assert manager.get_object_chunk("bush2") == (2, 2)

    def test_update_entity_position_same_chunk(
        self, large_world: World
    ) -> None:
        """Moving within same chunk updates version but not membership."""
        large_world.add_entity(
            Entity(entity_id="alice", position=Position(x=10, y=10))
        )

        manager = ChunkManager(large_world)
        manager.initialize_from_world()

        old_chunk, new_chunk = manager.update_entity_position(
            "alice", Position(x=10, y=10), Position(x=15, y=15)
        )

        # Same chunk, no change reported
        assert old_chunk is None
        assert new_chunk is None
        assert manager.get_entity_chunk("alice") == (0, 0)

    def test_update_entity_position_different_chunk(
        self, large_world: World
    ) -> None:
        """Moving to different chunk updates membership."""
        large_world.add_entity(
            Entity(entity_id="alice", position=Position(x=10, y=10))
        )

        manager = ChunkManager(large_world)
        manager.initialize_from_world()

        old_chunk, new_chunk = manager.update_entity_position(
            "alice", Position(x=10, y=10), Position(x=50, y=50)
        )

        assert old_chunk == (0, 0)
        assert new_chunk == (1, 1)
        assert manager.get_entity_chunk("alice") == (1, 1)

        # Old chunk no longer has entity
        chunk_0_0 = manager.get_chunk(0, 0)
        assert chunk_0_0 is not None
        assert "alice" not in chunk_0_0.entities

        # New chunk has entity
        chunk_1_1 = manager.get_chunk(1, 1)
        assert chunk_1_1 is not None
        assert "alice" in chunk_1_1.entities

    def test_add_and_remove_entity(self, manager: ChunkManager) -> None:
        """Entities can be added and removed from tracking."""
        chunk_key = manager.add_entity("test", Position(x=50, y=50))
        assert chunk_key == (1, 1)
        assert manager.get_entity_chunk("test") == (1, 1)

        removed_chunk = manager.remove_entity("test")
        assert removed_chunk == (1, 1)
        assert manager.get_entity_chunk("test") is None

    def test_get_entities_in_chunks(self, large_world: World) -> None:
        """Can query entities by chunk list."""
        large_world.add_entity(
            Entity(entity_id="alice", position=Position(x=10, y=10))
        )
        large_world.add_entity(
            Entity(entity_id="bob", position=Position(x=50, y=50))
        )
        large_world.add_entity(
            Entity(entity_id="charlie", position=Position(x=80, y=80))
        )

        manager = ChunkManager(large_world)
        manager.initialize_from_world()

        # Query chunks (0,0) and (1,1)
        entities = manager.get_entities_in_chunks([(0, 0), (1, 1)])
        assert entities == {"alice", "bob"}

        # Query all chunks
        entities = manager.get_entities_in_chunks([(0, 0), (1, 1), (2, 2)])
        assert entities == {"alice", "bob", "charlie"}


class TestTerrainChunkExtraction:
    """Tests for World.get_terrain_chunk()."""

    def test_get_terrain_chunk_with_floor_array(self) -> None:
        """Terrain is extracted from floor array."""
        world = World(width=64, height=64)
        floor = np.zeros((64, 64), dtype=np.uint8)
        floor[0:32, 0:32] = 3  # Grass in first chunk
        floor[0:32, 32:64] = 4  # Dirt in second chunk
        world.set_floor_array(floor)

        chunk_0_0 = world.get_terrain_chunk(0, 0)
        assert chunk_0_0.shape == (32, 32)
        assert np.all(chunk_0_0 == 3)

        chunk_1_0 = world.get_terrain_chunk(1, 0)
        assert np.all(chunk_1_0 == 4)

    def test_get_terrain_chunk_partial_world(self) -> None:
        """Edge chunks are padded with default value."""
        world = World(width=50, height=50)
        floor = np.full((50, 50), 3, dtype=np.uint8)
        world.set_floor_array(floor)

        # Chunk (1,1) starts at (32,32) and extends to (63,63)
        # But world only goes to (49,49), so partial
        chunk = world.get_terrain_chunk(1, 1)

        # Valid area (0:18, 0:18) should be grass
        assert np.all(chunk[0:18, 0:18] == 3)

        # Padding area should be default stone (6)
        assert np.all(chunk[18:, :] == 6)
        assert np.all(chunk[:, 18:] == 6)

    def test_get_terrain_chunk_without_floor_array(self) -> None:
        """Without floor array, returns default stone."""
        world = World(width=64, height=64)

        chunk = world.get_terrain_chunk(0, 0)
        assert np.all(chunk == 6)  # Default stone

    def test_get_terrain_chunk_with_sparse_overrides(self) -> None:
        """Sparse tile overrides are applied."""
        from world.state import Tile

        world = World(width=64, height=64)
        floor = np.full((64, 64), 3, dtype=np.uint8)  # All grass
        world.set_floor_array(floor)

        # Override a tile
        world.set_tile(
            Tile(position=Position(x=10, y=10), floor_type="mountain")
        )

        chunk = world.get_terrain_chunk(0, 0)

        # Most tiles are grass
        assert chunk[0, 0] == 3
        assert chunk[5, 5] == 3

        # Overridden tile is mountain
        assert chunk[10, 10] == 5
