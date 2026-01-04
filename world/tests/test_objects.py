"""Tests for WorldObject and World object registry."""

import pytest

from world.exceptions import ObjectAlreadyExistsError, ObjectNotFoundError
from world.state import World, WorldObject
from world.types import Position


class TestWorldObject:
    """Tests for WorldObject model."""

    def test_create_bush(self) -> None:
        """Create a bush object with state."""
        bush = WorldObject(
            object_id="bush1",
            position=Position(x=5, y=5),
            object_type="bush",
            state=(("berry_count", "5"), ("max_berries", "5")),
        )
        assert bush.object_id == "bush1"
        assert bush.position == Position(x=5, y=5)
        assert bush.object_type == "bush"
        assert bush.get_state("berry_count") == "5"
        assert bush.get_state("max_berries") == "5"

    def test_get_state_default(self) -> None:
        """get_state returns default for missing keys."""
        bush = WorldObject(
            object_id="bush1",
            position=Position(x=5, y=5),
            object_type="bush",
        )
        assert bush.get_state("nonexistent") == ""
        assert bush.get_state("nonexistent", "default") == "default"

    def test_with_state(self) -> None:
        """with_state creates new object with updated state."""
        bush = WorldObject(
            object_id="bush1",
            position=Position(x=5, y=5),
            object_type="bush",
            state=(("berry_count", "5"),),
        )
        bush2 = bush.with_state("berry_count", "3")

        # Original unchanged
        assert bush.get_state("berry_count") == "5"
        # New object has updated state
        assert bush2.get_state("berry_count") == "3"
        # Other fields unchanged
        assert bush2.object_id == "bush1"
        assert bush2.position == Position(x=5, y=5)

    def test_with_state_adds_new_key(self) -> None:
        """with_state can add new state keys."""
        bush = WorldObject(
            object_id="bush1",
            position=Position(x=5, y=5),
            object_type="bush",
        )
        bush2 = bush.with_state("new_key", "value")
        assert bush2.get_state("new_key") == "value"


class TestWorldObjectRegistry:
    """Tests for World object registry."""

    def test_add_and_get_object(self) -> None:
        """Add and retrieve an object."""
        world = World(width=10, height=10)
        bush = WorldObject(
            object_id="bush1",
            position=Position(x=5, y=5),
            object_type="bush",
        )
        world.add_object(bush)

        retrieved = world.get_object("bush1")
        assert retrieved == bush

    def test_add_duplicate_raises(self) -> None:
        """Adding duplicate object ID raises ObjectAlreadyExistsError."""
        world = World(width=10, height=10)
        bush = WorldObject(
            object_id="bush1",
            position=Position(x=5, y=5),
            object_type="bush",
        )
        world.add_object(bush)

        with pytest.raises(ObjectAlreadyExistsError, match="bush1"):
            world.add_object(bush)

    def test_get_nonexistent_raises(self) -> None:
        """Getting nonexistent object raises ObjectNotFoundError."""
        world = World(width=10, height=10)

        with pytest.raises(ObjectNotFoundError, match="bush1"):
            world.get_object("bush1")

    def test_get_objects_at_position(self) -> None:
        """Get all objects at a position."""
        world = World(width=10, height=10)
        pos = Position(x=3, y=3)

        bush1 = WorldObject(object_id="b1", position=pos, object_type="bush")
        bush2 = WorldObject(object_id="b2", position=pos, object_type="bush")
        world.add_object(bush1)
        world.add_object(bush2)

        objects = world.get_objects_at(pos)
        assert len(objects) == 2
        assert bush1 in objects
        assert bush2 in objects

    def test_get_objects_at_empty_position(self) -> None:
        """Get objects at position with no objects returns empty list."""
        world = World(width=10, height=10)
        objects = world.get_objects_at(Position(x=5, y=5))
        assert objects == []

    def test_update_object(self) -> None:
        """Update an existing object."""
        world = World(width=10, height=10)
        bush = WorldObject(
            object_id="bush1",
            position=Position(x=5, y=5),
            object_type="bush",
            state=(("berry_count", "5"),),
        )
        world.add_object(bush)

        updated = bush.with_state("berry_count", "3")
        world.update_object(updated)

        retrieved = world.get_object("bush1")
        assert retrieved.get_state("berry_count") == "3"

    def test_update_nonexistent_raises(self) -> None:
        """Updating nonexistent object raises ObjectNotFoundError."""
        world = World(width=10, height=10)
        bush = WorldObject(
            object_id="bush1",
            position=Position(x=5, y=5),
            object_type="bush",
        )

        with pytest.raises(ObjectNotFoundError, match="bush1"):
            world.update_object(bush)

    def test_all_objects(self) -> None:
        """all_objects returns all objects."""
        world = World(width=10, height=10)
        bush1 = WorldObject(
            object_id="b1", position=Position(x=1, y=1), object_type="bush"
        )
        bush2 = WorldObject(
            object_id="b2", position=Position(x=2, y=2), object_type="bush"
        )
        world.add_object(bush1)
        world.add_object(bush2)

        all_objs = world.all_objects()
        assert len(all_objs) == 2
        assert "b1" in all_objs
        assert "b2" in all_objs

    def test_object_count(self) -> None:
        """object_count returns number of objects."""
        world = World(width=10, height=10)
        assert world.object_count() == 0

        world.add_object(
            WorldObject(object_id="b1", position=Position(x=1, y=1), object_type="bush")
        )
        assert world.object_count() == 1

        world.add_object(
            WorldObject(object_id="b2", position=Position(x=2, y=2), object_type="bush")
        )
        assert world.object_count() == 2

    def test_objects_dont_block_entities(self) -> None:
        """Objects at a position don't prevent entity placement."""
        from world.state import Entity

        world = World(width=10, height=10)
        pos = Position(x=5, y=5)

        bush = WorldObject(object_id="bush1", position=pos, object_type="bush")
        world.add_object(bush)

        # Can still add entity at same position
        entity = Entity(entity_id="bob", position=pos)
        world.add_entity(entity)

        assert world.get_entity("bob").position == pos
        assert len(world.get_objects_at(pos)) == 1
