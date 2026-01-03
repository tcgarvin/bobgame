"""Tests for world state management."""

import pytest

from world.exceptions import (
    EntityAlreadyExistsError,
    EntityNotFoundError,
    PositionOccupiedError,
)
from world.state import Entity, Tile, World
from world.types import Position


class TestTile:
    """Tests for Tile class."""

    def test_tile_defaults(self):
        """Tile has sensible defaults."""
        tile = Tile(position=Position(x=0, y=0))
        assert tile.walkable is True
        assert tile.opaque is False
        assert tile.floor_type == "stone"

    def test_tile_custom_values(self):
        """Tile can be created with custom values."""
        tile = Tile(
            position=Position(x=5, y=5),
            walkable=False,
            opaque=True,
            floor_type="wall",
        )
        assert tile.walkable is False
        assert tile.opaque is True
        assert tile.floor_type == "wall"

    def test_tile_immutable(self):
        """Tile is immutable."""
        tile = Tile(position=Position(x=0, y=0))
        with pytest.raises(Exception):
            tile.walkable = False  # type: ignore


class TestEntity:
    """Tests for Entity class."""

    def test_entity_creation(self):
        """Entity can be created with required fields."""
        entity = Entity(entity_id="player1", position=Position(x=5, y=5))
        assert entity.entity_id == "player1"
        assert entity.position == Position(x=5, y=5)

    def test_entity_defaults(self):
        """Entity has sensible defaults."""
        entity = Entity(entity_id="test", position=Position(x=0, y=0))
        assert entity.entity_type == "default"
        assert entity.tags == ()
        assert entity.status_bits == 0

    def test_entity_with_position(self):
        """Entity.with_position creates new entity with updated position."""
        original = Entity(
            entity_id="player1",
            position=Position(x=5, y=5),
            entity_type="player",
            tags=("hero", "human"),
        )
        moved = original.with_position(Position(x=6, y=5))

        # New entity has new position
        assert moved.position == Position(x=6, y=5)

        # Other fields preserved
        assert moved.entity_id == "player1"
        assert moved.entity_type == "player"
        assert moved.tags == ("hero", "human")

        # Original unchanged
        assert original.position == Position(x=5, y=5)

    def test_entity_immutable(self):
        """Entity is immutable."""
        entity = Entity(entity_id="test", position=Position(x=0, y=0))
        with pytest.raises(Exception):
            entity.position = Position(x=1, y=1)  # type: ignore


class TestWorld:
    """Tests for World class."""

    def test_world_creation(self):
        """World can be created with dimensions."""
        world = World(width=100, height=50)
        assert world.width == 100
        assert world.height == 50
        assert world.tick == 0

    def test_world_bounds_checking(self):
        """World correctly checks bounds."""
        world = World(width=10, height=10)

        # In bounds
        assert world.in_bounds(Position(x=0, y=0)) is True
        assert world.in_bounds(Position(x=9, y=9)) is True
        assert world.in_bounds(Position(x=5, y=5)) is True

        # Out of bounds
        assert world.in_bounds(Position(x=-1, y=0)) is False
        assert world.in_bounds(Position(x=0, y=-1)) is False
        assert world.in_bounds(Position(x=10, y=0)) is False
        assert world.in_bounds(Position(x=0, y=10)) is False


class TestWorldTiles:
    """Tests for World tile operations."""

    def test_default_tiles_walkable(self, empty_world: World):
        """Unset tiles are walkable by default."""
        assert empty_world.is_walkable(Position(x=5, y=5)) is True
        assert empty_world.get_tile(Position(x=5, y=5)).walkable is True

    def test_out_of_bounds_not_walkable(self, empty_world: World):
        """Out of bounds positions are not walkable."""
        assert empty_world.is_walkable(Position(x=-1, y=0)) is False
        assert empty_world.is_walkable(Position(x=100, y=100)) is False

    def test_set_tile(self, empty_world: World):
        """Can set custom tile properties."""
        wall = Tile(position=Position(x=5, y=5), walkable=False, opaque=True)
        empty_world.set_tile(wall)

        assert empty_world.is_walkable(Position(x=5, y=5)) is False
        assert empty_world.get_tile(Position(x=5, y=5)).opaque is True

    def test_wall_fixture(self, world_with_walls: World):
        """Wall fixture has wall at y=5."""
        for x in range(10):
            assert world_with_walls.is_walkable(Position(x=x, y=5)) is False
            assert world_with_walls.is_walkable(Position(x=x, y=4)) is True
            assert world_with_walls.is_walkable(Position(x=x, y=6)) is True


class TestWorldEntities:
    """Tests for World entity operations."""

    def test_add_entity(self, empty_world: World):
        """Can add entity to world."""
        entity = Entity(entity_id="player1", position=Position(x=5, y=5))
        empty_world.add_entity(entity)

        assert empty_world.entity_count() == 1
        assert empty_world.get_entity("player1") == entity

    def test_add_duplicate_entity_raises(self, empty_world: World):
        """Adding entity with duplicate ID raises."""
        entity1 = Entity(entity_id="player1", position=Position(x=5, y=5))
        entity2 = Entity(entity_id="player1", position=Position(x=6, y=6))

        empty_world.add_entity(entity1)
        with pytest.raises(EntityAlreadyExistsError):
            empty_world.add_entity(entity2)

    def test_add_entity_to_occupied_position_raises(self, empty_world: World):
        """Adding entity to occupied position raises."""
        entity1 = Entity(entity_id="player1", position=Position(x=5, y=5))
        entity2 = Entity(entity_id="player2", position=Position(x=5, y=5))

        empty_world.add_entity(entity1)
        with pytest.raises(PositionOccupiedError):
            empty_world.add_entity(entity2)

    def test_get_entity_not_found_raises(self, empty_world: World):
        """Getting non-existent entity raises."""
        with pytest.raises(EntityNotFoundError):
            empty_world.get_entity("nonexistent")

    def test_get_entity_at(self, empty_world: World):
        """Can get entity by position."""
        entity = Entity(entity_id="player1", position=Position(x=5, y=5))
        empty_world.add_entity(entity)

        assert empty_world.get_entity_at(Position(x=5, y=5)) == entity
        assert empty_world.get_entity_at(Position(x=6, y=6)) is None

    def test_remove_entity(self, empty_world: World):
        """Can remove entity from world."""
        entity = Entity(entity_id="player1", position=Position(x=5, y=5))
        empty_world.add_entity(entity)

        removed = empty_world.remove_entity("player1")
        assert removed == entity
        assert empty_world.entity_count() == 0
        assert empty_world.get_entity_at(Position(x=5, y=5)) is None

    def test_remove_nonexistent_entity_raises(self, empty_world: World):
        """Removing non-existent entity raises."""
        with pytest.raises(EntityNotFoundError):
            empty_world.remove_entity("nonexistent")

    def test_update_entity_position(self, empty_world: World):
        """Can update entity position."""
        entity = Entity(entity_id="player1", position=Position(x=5, y=5))
        empty_world.add_entity(entity)

        empty_world.update_entity_position("player1", Position(x=6, y=5))

        updated = empty_world.get_entity("player1")
        assert updated.position == Position(x=6, y=5)
        assert empty_world.get_entity_at(Position(x=5, y=5)) is None
        assert empty_world.get_entity_at(Position(x=6, y=5)) == updated

    def test_update_nonexistent_entity_raises(self, empty_world: World):
        """Updating non-existent entity raises."""
        with pytest.raises(EntityNotFoundError):
            empty_world.update_entity_position("nonexistent", Position(x=0, y=0))

    def test_is_position_occupied(self, empty_world: World):
        """Can check if position is occupied."""
        assert empty_world.is_position_occupied(Position(x=5, y=5)) is False

        entity = Entity(entity_id="player1", position=Position(x=5, y=5))
        empty_world.add_entity(entity)

        assert empty_world.is_position_occupied(Position(x=5, y=5)) is True
        assert empty_world.is_position_occupied(Position(x=6, y=6)) is False

    def test_all_entities(self, two_entities: World):
        """Can get all entities."""
        entities = two_entities.all_entities()
        assert len(entities) == 2
        assert "entity_a" in entities
        assert "entity_b" in entities


class TestWorldTick:
    """Tests for World tick operations."""

    def test_advance_tick(self, empty_world: World):
        """Tick counter increments."""
        assert empty_world.tick == 0
        empty_world.advance_tick()
        assert empty_world.tick == 1
        empty_world.advance_tick()
        assert empty_world.tick == 2

    def test_tick_starts_at_zero(self):
        """New world starts at tick 0."""
        world = World(width=10, height=10)
        assert world.tick == 0
