"""Tests for foraging system (collect, eat, regeneration).

Berry bushes have binary state: either has a berry (1) or doesn't (0).
"""

from world.events import TickEvents
from world.foraging import (
    process_collect_phase,
    process_eat_phase,
    process_regeneration,
)
from world.state import Entity, Inventory, World, WorldObject
from world.types import CollectIntent, EatIntent, Position


def _collect(world: World, intents: dict[str, CollectIntent]) -> TickEvents:
    """Run the collect phase and return everything it recorded."""
    events = TickEvents()
    process_collect_phase(world, intents, events)
    return events


def _eat(world: World, intents: dict[str, EatIntent]) -> TickEvents:
    """Run the eat phase and return everything it recorded."""
    events = TickEvents()
    process_eat_phase(world, intents, events)
    return events


def _regenerate(world: World, regen_rate: int = 10) -> TickEvents:
    """Run bush regeneration and return everything it recorded."""
    events = TickEvents()
    process_regeneration(world, events, regen_rate=regen_rate)
    return events


def _bush(object_id: str, position: Position, berry: str) -> WorldObject:
    return WorldObject(
        object_id=object_id,
        position=position,
        object_type="bush",
        state=(("berry_count", berry),),
    )


class TestCollectPhase:
    """Tests for berry collection from bushes."""

    def test_collect_success(self) -> None:
        """Entity collects the berry from a bush."""
        world = World(width=10, height=10)
        pos = Position(x=5, y=5)
        world.add_entity(Entity(entity_id="bob", position=pos))
        world.add_object(_bush("bush1", pos, "1"))

        intents = {
            "bob": CollectIntent(entity_id="bob", object_id="bush1", item_type="berry")
        }
        events = _collect(world, intents)

        assert len(events.action_results) == 1
        action = events.action_results[0]
        assert action.entity_id == "bob"
        assert action.action_type == "collect"
        assert action.success
        assert action.details == "collected berry from bush1"

        assert world.get_entity("bob").inventory.count("berry") == 1

        assert len(events.object_changes) == 1
        assert events.object_changes[0].object_id == "bush1"
        assert events.object_changes[0].new_value == "0"

    def test_collect_from_empty_bush(self) -> None:
        """Cannot collect from empty bush."""
        world = World(width=10, height=10)
        pos = Position(x=5, y=5)
        world.add_entity(Entity(entity_id="bob", position=pos))
        world.add_object(_bush("bush1", pos, "0"))

        events = _collect(
            world,
            {
                "bob": CollectIntent(
                    entity_id="bob", object_id="bush1", item_type="berry"
                )
            },
        )

        assert len(events.action_results) == 1
        assert not events.action_results[0].success
        assert events.action_results[0].details == "no_berries"
        assert events.object_changes == []

    def test_collect_not_at_bush(self) -> None:
        """Cannot collect if not at the same position as bush."""
        world = World(width=10, height=10)
        world.add_entity(Entity(entity_id="bob", position=Position(x=1, y=1)))
        world.add_object(_bush("bush1", Position(x=5, y=5), "1"))

        events = _collect(
            world,
            {
                "bob": CollectIntent(
                    entity_id="bob", object_id="bush1", item_type="berry"
                )
            },
        )

        assert len(events.action_results) == 1
        assert not events.action_results[0].success
        assert events.action_results[0].details == "object_not_at_position"

    def test_collect_nonexistent_object(self) -> None:
        """Cannot collect from nonexistent object."""
        world = World(width=10, height=10)
        world.add_entity(Entity(entity_id="bob", position=Position(x=5, y=5)))

        events = _collect(
            world,
            {
                "bob": CollectIntent(
                    entity_id="bob", object_id="nonexistent", item_type="berry"
                )
            },
        )

        assert len(events.action_results) == 1
        assert not events.action_results[0].success
        assert events.action_results[0].details == "object_not_found"

    def test_collect_multiple_entities_different_bushes(self) -> None:
        """Multiple entities collecting from different bushes simultaneously."""
        world = World(width=10, height=10)
        world.add_entity(Entity(entity_id="alice", position=Position(x=2, y=2)))
        world.add_entity(Entity(entity_id="zack", position=Position(x=7, y=7)))
        world.add_object(_bush("bush1", Position(x=2, y=2), "1"))
        world.add_object(_bush("bush2", Position(x=7, y=7), "1"))

        events = _collect(
            world,
            {
                "alice": CollectIntent(
                    entity_id="alice", object_id="bush1", item_type="berry"
                ),
                "zack": CollectIntent(
                    entity_id="zack", object_id="bush2", item_type="berry"
                ),
            },
        )

        assert all(action.success for action in events.action_results)
        assert world.get_entity("alice").inventory.count("berry") == 1
        assert world.get_entity("zack").inventory.count("berry") == 1
        assert world.get_object("bush1").get_state("berry_count") == "0"
        assert world.get_object("bush2").get_state("berry_count") == "0"

    def test_collect_entity_not_at_bush_fails(self) -> None:
        """Entity trying to collect from bush at different position fails."""
        world = World(width=10, height=10)
        bush_pos = Position(x=5, y=5)
        world.add_entity(Entity(entity_id="alice", position=bush_pos))
        world.add_entity(Entity(entity_id="zack", position=Position(x=5, y=6)))
        world.add_object(_bush("bush1", bush_pos, "1"))

        events = _collect(
            world,
            {
                "alice": CollectIntent(
                    entity_id="alice", object_id="bush1", item_type="berry"
                ),
                "zack": CollectIntent(
                    entity_id="zack", object_id="bush1", item_type="berry"
                ),
            },
        )

        by_entity = {action.entity_id: action for action in events.action_results}
        assert by_entity["alice"].success
        assert not by_entity["zack"].success
        assert by_entity["zack"].details == "object_not_at_position"

        assert world.get_entity("alice").inventory.count("berry") == 1
        assert world.get_entity("zack").inventory.count("berry") == 0

    def test_collect_auto_find_bush(self) -> None:
        """Can collect without specifying object_id if bush is at position."""
        world = World(width=10, height=10)
        pos = Position(x=5, y=5)
        world.add_entity(Entity(entity_id="bob", position=pos))
        world.add_object(_bush("bush1", pos, "1"))

        events = _collect(
            world,
            {"bob": CollectIntent(entity_id="bob", object_id="", item_type="berry")},
        )

        assert len(events.action_results) == 1
        assert events.action_results[0].success
        assert events.action_results[0].details == "collected berry from bush1"


class TestEatPhase:
    """Tests for eating items from inventory."""

    def test_eat_success(self) -> None:
        """Entity eats berries from inventory."""
        world = World(width=10, height=10)
        world.add_entity(
            Entity(
                entity_id="bob",
                position=Position(x=5, y=5),
                inventory=Inventory().add("berry", 5),
            )
        )

        events = _eat(
            world, {"bob": EatIntent(entity_id="bob", item_type="berry", amount=2)}
        )

        assert len(events.action_results) == 1
        action = events.action_results[0]
        assert action.entity_id == "bob"
        assert action.action_type == "eat"
        assert action.success
        assert action.details == "ate 2 berry"

        assert world.get_entity("bob").inventory.count("berry") == 3

    def test_eat_all_items(self) -> None:
        """Eating all items leaves inventory empty."""
        world = World(width=10, height=10)
        world.add_entity(
            Entity(
                entity_id="bob",
                position=Position(x=5, y=5),
                inventory=Inventory().add("berry", 3),
            )
        )

        events = _eat(
            world, {"bob": EatIntent(entity_id="bob", item_type="berry", amount=3)}
        )

        assert events.action_results[0].success
        assert world.get_entity("bob").inventory.count("berry") == 0

    def test_eat_insufficient_items(self) -> None:
        """Cannot eat more than you have."""
        world = World(width=10, height=10)
        world.add_entity(
            Entity(
                entity_id="bob",
                position=Position(x=5, y=5),
                inventory=Inventory().add("berry", 2),
            )
        )

        events = _eat(
            world, {"bob": EatIntent(entity_id="bob", item_type="berry", amount=5)}
        )

        assert len(events.action_results) == 1
        assert not events.action_results[0].success
        assert events.action_results[0].details == "insufficient_items"
        assert world.get_entity("bob").inventory.count("berry") == 2

    def test_eat_no_items(self) -> None:
        """Cannot eat items you don't have."""
        world = World(width=10, height=10)
        world.add_entity(Entity(entity_id="bob", position=Position(x=5, y=5)))

        events = _eat(
            world, {"bob": EatIntent(entity_id="bob", item_type="berry", amount=1)}
        )

        assert len(events.action_results) == 1
        assert not events.action_results[0].success
        assert events.action_results[0].details == "insufficient_items"


class TestRegeneration:
    """Tests for bush berry regeneration.

    Berry bushes have binary state: either has a berry (1) or doesn't (0).
    Regeneration sets empty bushes to have a berry.
    """

    def test_regeneration_adds_berry(self) -> None:
        """Empty bushes regenerate a berry."""
        world = World(width=10, height=10, tick=10)
        world.add_object(_bush("bush1", Position(x=5, y=5), "0"))

        changes = _regenerate(world).object_changes

        assert len(changes) == 1
        assert changes[0].object_id == "bush1"
        assert changes[0].old_value == "0"
        assert changes[0].new_value == "1"

    def test_regeneration_skips_full_bush(self) -> None:
        """Bushes with a berry don't regenerate."""
        world = World(width=10, height=10, tick=10)
        world.add_object(_bush("bush1", Position(x=5, y=5), "1"))

        assert _regenerate(world).object_changes == []

    def test_regeneration_skips_non_interval_ticks(self) -> None:
        """Regeneration only happens at interval ticks."""
        world = World(width=10, height=10, tick=5)
        world.add_object(_bush("bush1", Position(x=5, y=5), "0"))

        assert _regenerate(world).object_changes == []
        assert world.get_object("bush1").get_state("berry_count") == "0"

    def test_regeneration_at_zero(self) -> None:
        """Regeneration happens at tick 0 (0 % N == 0)."""
        world = World(width=10, height=10, tick=0)
        world.add_object(_bush("bush1", Position(x=5, y=5), "0"))

        assert len(_regenerate(world).object_changes) == 1
        assert world.get_object("bush1").get_state("berry_count") == "1"

    def test_regeneration_of_a_bush_with_no_berry_key(self) -> None:
        """A bush that never stored `berry_count` regenerates from "0"."""
        world = World(width=10, height=10, tick=10)
        world.add_object(
            WorldObject(
                object_id="bush1", position=Position(x=5, y=5), object_type="bush"
            )
        )

        changes = _regenerate(world).object_changes

        assert len(changes) == 1
        assert changes[0].old_value == "0"
        assert changes[0].new_value == "1"

    def test_regeneration_multiple_bushes(self) -> None:
        """Multiple empty bushes regenerate together."""
        world = World(width=10, height=10, tick=10)
        world.add_object(_bush("bush1", Position(x=1, y=1), "0"))
        world.add_object(_bush("bush2", Position(x=2, y=2), "0"))

        changes = _regenerate(world).object_changes

        assert len(changes) == 2
        assert {c.object_id for c in changes} == {"bush1", "bush2"}

    def test_regeneration_mixed_bushes(self) -> None:
        """Only empty bushes regenerate when mixed."""
        world = World(width=10, height=10, tick=10)
        world.add_object(_bush("bush1", Position(x=1, y=1), "0"))
        world.add_object(_bush("bush2", Position(x=2, y=2), "1"))

        changes = _regenerate(world).object_changes

        assert len(changes) == 1
        assert changes[0].object_id == "bush1"
