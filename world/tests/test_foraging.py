"""Tests for foraging system (collect, eat, regeneration)."""

import pytest

from world.foraging import (
    CollectResult,
    EatResult,
    ObjectChange,
    process_collect_phase,
    process_eat_phase,
    process_regeneration,
)
from world.state import Entity, Inventory, World, WorldObject
from world.types import CollectIntent, EatIntent, Position


class TestCollectPhase:
    """Tests for berry collection from bushes."""

    def test_collect_success(self) -> None:
        """Entity collects berries from a bush."""
        world = World(width=10, height=10)
        pos = Position(x=5, y=5)

        entity = Entity(entity_id="bob", position=pos)
        world.add_entity(entity)

        bush = WorldObject(
            object_id="bush1",
            position=pos,
            object_type="bush",
            state=(("berry_count", "5"), ("max_berries", "5")),
        )
        world.add_object(bush)

        intents = {
            "bob": CollectIntent(
                entity_id="bob", object_id="bush1", item_type="berry", amount=2
            )
        }

        results, object_changes = process_collect_phase(world, intents)

        assert len(results) == 1
        assert results[0].entity_id == "bob"
        assert results[0].success
        assert results[0].amount == 2
        assert results[0].item_type == "berry"

        # Check entity inventory was updated in world
        updated_entity = world.get_entity("bob")
        assert updated_entity.inventory.count("berry") == 2

        # Check object was updated
        assert len(object_changes) == 1
        assert object_changes[0].object_id == "bush1"
        assert object_changes[0].new_value == "3"

    def test_collect_from_empty_bush(self) -> None:
        """Cannot collect from empty bush."""
        world = World(width=10, height=10)
        pos = Position(x=5, y=5)

        entity = Entity(entity_id="bob", position=pos)
        world.add_entity(entity)

        bush = WorldObject(
            object_id="bush1",
            position=pos,
            object_type="bush",
            state=(("berry_count", "0"), ("max_berries", "5")),
        )
        world.add_object(bush)

        intents = {
            "bob": CollectIntent(
                entity_id="bob", object_id="bush1", item_type="berry", amount=1
            )
        }

        results, object_changes = process_collect_phase(world, intents)

        assert len(results) == 1
        assert not results[0].success
        assert results[0].failure_reason == "no_berries"
        assert len(object_changes) == 0

    def test_collect_partial_amount(self) -> None:
        """Collect less than requested if bush doesn't have enough."""
        world = World(width=10, height=10)
        pos = Position(x=5, y=5)

        entity = Entity(entity_id="bob", position=pos)
        world.add_entity(entity)

        bush = WorldObject(
            object_id="bush1",
            position=pos,
            object_type="bush",
            state=(("berry_count", "2"), ("max_berries", "5")),
        )
        world.add_object(bush)

        intents = {
            "bob": CollectIntent(
                entity_id="bob", object_id="bush1", item_type="berry", amount=5
            )
        }

        results, object_changes = process_collect_phase(world, intents)

        assert len(results) == 1
        assert results[0].success
        assert results[0].amount == 2  # Only got 2, not 5

        updated_entity = world.get_entity("bob")
        assert updated_entity.inventory.count("berry") == 2

        updated_bush = world.get_object("bush1")
        assert updated_bush.get_state("berry_count") == "0"

    def test_collect_not_at_bush(self) -> None:
        """Cannot collect if not at the same position as bush."""
        world = World(width=10, height=10)

        entity = Entity(entity_id="bob", position=Position(x=1, y=1))
        world.add_entity(entity)

        bush = WorldObject(
            object_id="bush1",
            position=Position(x=5, y=5),
            object_type="bush",
            state=(("berry_count", "5"), ("max_berries", "5")),
        )
        world.add_object(bush)

        intents = {
            "bob": CollectIntent(
                entity_id="bob", object_id="bush1", item_type="berry", amount=1
            )
        }

        results, object_changes = process_collect_phase(world, intents)

        assert len(results) == 1
        assert not results[0].success
        assert results[0].failure_reason == "object_not_at_position"

    def test_collect_nonexistent_object(self) -> None:
        """Cannot collect from nonexistent object."""
        world = World(width=10, height=10)

        entity = Entity(entity_id="bob", position=Position(x=5, y=5))
        world.add_entity(entity)

        intents = {
            "bob": CollectIntent(
                entity_id="bob", object_id="nonexistent", item_type="berry", amount=1
            )
        }

        results, object_changes = process_collect_phase(world, intents)

        assert len(results) == 1
        assert not results[0].success
        assert results[0].failure_reason == "object_not_found"

    def test_collect_multiple_entities_different_bushes(self) -> None:
        """Multiple entities collecting from different bushes simultaneously."""
        world = World(width=10, height=10)

        # Two entities at different positions with their own bushes
        entity_a = Entity(entity_id="alice", position=Position(x=2, y=2))
        entity_z = Entity(entity_id="zack", position=Position(x=7, y=7))
        world.add_entity(entity_a)
        world.add_entity(entity_z)

        bush1 = WorldObject(
            object_id="bush1",
            position=Position(x=2, y=2),
            object_type="bush",
            state=(("berry_count", "3"), ("max_berries", "5")),
        )
        bush2 = WorldObject(
            object_id="bush2",
            position=Position(x=7, y=7),
            object_type="bush",
            state=(("berry_count", "5"), ("max_berries", "5")),
        )
        world.add_object(bush1)
        world.add_object(bush2)

        intents = {
            "alice": CollectIntent(
                entity_id="alice", object_id="bush1", item_type="berry", amount=2
            ),
            "zack": CollectIntent(
                entity_id="zack", object_id="bush2", item_type="berry", amount=3
            ),
        }

        results, object_changes = process_collect_phase(world, intents)

        # Both should succeed
        alice_result = next(r for r in results if r.entity_id == "alice")
        zack_result = next(r for r in results if r.entity_id == "zack")

        assert alice_result.success
        assert alice_result.amount == 2
        assert zack_result.success
        assert zack_result.amount == 3

        alice_entity = world.get_entity("alice")
        zack_entity = world.get_entity("zack")
        assert alice_entity.inventory.count("berry") == 2
        assert zack_entity.inventory.count("berry") == 3

        assert world.get_object("bush1").get_state("berry_count") == "1"
        assert world.get_object("bush2").get_state("berry_count") == "2"

    def test_collect_auto_find_bush(self) -> None:
        """Can collect without specifying object_id if bush is at position."""
        world = World(width=10, height=10)
        pos = Position(x=5, y=5)

        entity = Entity(entity_id="bob", position=pos)
        world.add_entity(entity)

        bush = WorldObject(
            object_id="bush1",
            position=pos,
            object_type="bush",
            state=(("berry_count", "5"), ("max_berries", "5")),
        )
        world.add_object(bush)

        # No object_id specified (empty string)
        intents = {
            "bob": CollectIntent(entity_id="bob", object_id="", item_type="berry", amount=1)
        }

        results, object_changes = process_collect_phase(world, intents)

        assert len(results) == 1
        assert results[0].success
        assert results[0].object_id == "bush1"


class TestEatPhase:
    """Tests for eating items from inventory."""

    def test_eat_success(self) -> None:
        """Entity eats berries from inventory."""
        world = World(width=10, height=10)

        entity = Entity(
            entity_id="bob",
            position=Position(x=5, y=5),
            inventory=Inventory().add("berry", 5),
        )
        world.add_entity(entity)

        intents = {"bob": EatIntent(entity_id="bob", item_type="berry", amount=2)}

        results = process_eat_phase(world, intents)

        assert len(results) == 1
        assert results[0].entity_id == "bob"
        assert results[0].success
        assert results[0].amount == 2

        updated_entity = world.get_entity("bob")
        assert updated_entity.inventory.count("berry") == 3

    def test_eat_all_items(self) -> None:
        """Eating all items leaves inventory empty."""
        world = World(width=10, height=10)

        entity = Entity(
            entity_id="bob",
            position=Position(x=5, y=5),
            inventory=Inventory().add("berry", 3),
        )
        world.add_entity(entity)

        intents = {"bob": EatIntent(entity_id="bob", item_type="berry", amount=3)}

        results = process_eat_phase(world, intents)

        assert results[0].success
        updated_entity = world.get_entity("bob")
        assert updated_entity.inventory.count("berry") == 0

    def test_eat_insufficient_items(self) -> None:
        """Cannot eat more than you have."""
        world = World(width=10, height=10)

        entity = Entity(
            entity_id="bob",
            position=Position(x=5, y=5),
            inventory=Inventory().add("berry", 2),
        )
        world.add_entity(entity)

        intents = {"bob": EatIntent(entity_id="bob", item_type="berry", amount=5)}

        results = process_eat_phase(world, intents)

        assert len(results) == 1
        assert not results[0].success
        assert results[0].failure_reason == "insufficient_items"

        # Inventory unchanged
        entity = world.get_entity("bob")
        assert entity.inventory.count("berry") == 2

    def test_eat_no_items(self) -> None:
        """Cannot eat items you don't have."""
        world = World(width=10, height=10)

        entity = Entity(entity_id="bob", position=Position(x=5, y=5))
        world.add_entity(entity)

        intents = {"bob": EatIntent(entity_id="bob", item_type="berry", amount=1)}

        results = process_eat_phase(world, intents)

        assert len(results) == 1
        assert not results[0].success
        assert results[0].failure_reason == "insufficient_items"


class TestRegeneration:
    """Tests for bush berry regeneration."""

    def test_regeneration_adds_berry(self) -> None:
        """Bushes regenerate berries over time."""
        world = World(width=10, height=10, tick=10)

        bush = WorldObject(
            object_id="bush1",
            position=Position(x=5, y=5),
            object_type="bush",
            state=(("berry_count", "3"), ("max_berries", "5")),
        )
        world.add_object(bush)

        object_changes = process_regeneration(world, regen_rate=10)

        assert len(object_changes) == 1
        assert object_changes[0].object_id == "bush1"
        assert object_changes[0].new_value == "4"

    def test_regeneration_respects_max(self) -> None:
        """Full bushes don't regenerate beyond max."""
        world = World(width=10, height=10, tick=10)

        bush = WorldObject(
            object_id="bush1",
            position=Position(x=5, y=5),
            object_type="bush",
            state=(("berry_count", "5"), ("max_berries", "5")),
        )
        world.add_object(bush)

        object_changes = process_regeneration(world, regen_rate=10)

        # No changes for full bush
        assert len(object_changes) == 0

    def test_regeneration_skips_non_interval_ticks(self) -> None:
        """Regeneration only happens at interval ticks."""
        # Test tick 5 - not a multiple of 10, should not regenerate
        world = World(width=10, height=10, tick=5)
        bush = WorldObject(
            object_id="bush1",
            position=Position(x=5, y=5),
            object_type="bush",
            state=(("berry_count", "3"), ("max_berries", "5")),
        )
        world.add_object(bush)

        changes = process_regeneration(world, regen_rate=10)
        assert len(changes) == 0

        # Verify bush unchanged
        assert world.get_object("bush1").get_state("berry_count") == "3"

    def test_regeneration_at_zero(self) -> None:
        """Regeneration happens at tick 0 (0 % N == 0)."""
        world = World(width=10, height=10, tick=0)
        bush = WorldObject(
            object_id="bush1",
            position=Position(x=5, y=5),
            object_type="bush",
            state=(("berry_count", "3"), ("max_berries", "5")),
        )
        world.add_object(bush)

        changes = process_regeneration(world, regen_rate=10)
        assert len(changes) == 1
        assert world.get_object("bush1").get_state("berry_count") == "4"

    def test_regeneration_multiple_bushes(self) -> None:
        """Multiple bushes regenerate together."""
        world = World(width=10, height=10, tick=10)

        bush1 = WorldObject(
            object_id="bush1",
            position=Position(x=1, y=1),
            object_type="bush",
            state=(("berry_count", "2"), ("max_berries", "5")),
        )
        bush2 = WorldObject(
            object_id="bush2",
            position=Position(x=2, y=2),
            object_type="bush",
            state=(("berry_count", "4"), ("max_berries", "5")),
        )
        world.add_object(bush1)
        world.add_object(bush2)

        object_changes = process_regeneration(world, regen_rate=10)

        assert len(object_changes) == 2
        ids = {c.object_id for c in object_changes}
        assert ids == {"bush1", "bush2"}
