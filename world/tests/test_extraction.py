"""Tests for tree chopping and rock mining."""

from world.events import TickEvents
from world.foraging import process_extract_phase
from world.items import default_remaining
from world.state import Entity, Inventory, World, WorldObject
from world.types import ExtractIntent, Position


def _world_with_tree(wielded: str = "") -> World:
    world = World(width=10, height=10)
    world.add_entity(
        Entity(
            entity_id="bob",
            position=Position(x=5, y=5),
            inventory=Inventory().add("axe", 1),
            wielded=wielded,
        )
    )
    world.add_object(
        WorldObject(object_id="tree_1", position=Position(x=6, y=5), object_type="tree")
    )
    return world


def _extract(world: World, *entity_ids: str) -> TickEvents:
    events = TickEvents()
    process_extract_phase(
        world,
        {
            entity_id: ExtractIntent(entity_id=entity_id, object_id="tree_1")
            for entity_id in entity_ids
        },
        events,
    )
    return events


class TestExtractDefaults:
    def test_lazy_defaults_by_type(self) -> None:
        assert default_remaining("tree") == 4
        assert default_remaining("rock_small") == 1
        assert default_remaining("rock_medium") == 2
        assert default_remaining("rock_large") == 4
        assert default_remaining("boulder") == 6


class TestExtractThresholds:
    def test_bare_handed_needs_three_ticks(self) -> None:
        world = _world_with_tree()

        _extract(world, "bob")
        assert world.get_entity("bob").inventory.count("wood") == 0
        assert world.get_object("tree_1").get_state("progress") == "1"

        _extract(world, "bob")
        assert world.get_entity("bob").inventory.count("wood") == 0

        _extract(world, "bob")
        assert world.get_entity("bob").inventory.count("wood") == 1
        assert world.get_object("tree_1").get_state("progress") == "0"
        assert world.get_object("tree_1").get_state("remaining") == "3"

    def test_axe_yields_one_wood_per_tick(self) -> None:
        world = _world_with_tree(wielded="axe")

        events = _extract(world, "bob")

        assert world.get_entity("bob").inventory.count("wood") == 1
        assert "+1 wood" in events.action_results[0].details

    def test_wrong_tool_is_bare_handed(self) -> None:
        world = _world_with_tree(wielded="pickaxe")
        _extract(world, "bob")
        assert world.get_object("tree_1").get_state("progress") == "1"

    def test_object_removed_when_depleted(self) -> None:
        world = _world_with_tree(wielded="axe")

        for _ in range(4):
            events = _extract(world, "bob")

        assert "tree_1" not in world.all_objects()
        assert [e.object_id for e in events.objects_removed] == ["tree_1"]
        assert world.get_entity("bob").inventory.count("wood") == 4

    def test_rock_yields_stone(self) -> None:
        world = World(width=10, height=10)
        world.add_entity(
            Entity(
                entity_id="bob",
                position=Position(x=1, y=1),
                inventory=Inventory().add("pickaxe", 1),
                wielded="pickaxe",
            )
        )
        world.add_object(
            WorldObject(
                object_id="rock_1",
                position=Position(x=1, y=2),
                object_type="rock_small",
            )
        )
        events = TickEvents()

        process_extract_phase(
            world,
            {"bob": ExtractIntent(entity_id="bob", object_id="rock_1")},
            events,
        )

        assert world.get_entity("bob").inventory.count("stone") == 1
        assert "rock_1" not in world.all_objects()


class TestExtractValidation:
    def test_distant_object_fails(self) -> None:
        world = _world_with_tree()
        world.update_entity_position("bob", Position(x=0, y=0))

        events = _extract(world, "bob")

        assert not events.action_results[0].success
        assert "not adjacent" in events.action_results[0].details

    def test_bush_cannot_be_extracted(self) -> None:
        world = _world_with_tree()
        world.add_object(
            WorldObject(
                object_id="bush_1", position=Position(x=5, y=5), object_type="bush"
            )
        )
        events = TickEvents()

        process_extract_phase(
            world,
            {"bob": ExtractIntent(entity_id="bob", object_id="bush_1")},
            events,
        )

        assert not events.action_results[0].success

    def test_missing_object_fails(self) -> None:
        world = _world_with_tree()
        events = TickEvents()

        process_extract_phase(
            world,
            {"bob": ExtractIntent(entity_id="bob", object_id="nope")},
            events,
        )

        assert not events.action_results[0].success


class TestExtractConflicts:
    def test_lexicographic_winner_takes_the_unit(self) -> None:
        world = _world_with_tree()
        world.add_entity(Entity(entity_id="alice", position=Position(x=6, y=6)))
        # Two bare-handed workers add 1 each; progress starts at 2 so the
        # first (lexicographic) worker crosses the threshold.
        world.update_object(world.get_object("tree_1").with_state("progress", "2"))

        _extract(world, "bob", "alice")

        assert world.get_entity("alice").inventory.count("wood") == 1
        assert world.get_entity("bob").inventory.count("wood") == 0
        assert world.get_object("tree_1").get_state("progress") == "1"

    def test_both_progress_the_same_object(self) -> None:
        world = _world_with_tree()
        world.add_entity(Entity(entity_id="alice", position=Position(x=6, y=6)))

        _extract(world, "bob", "alice")

        assert world.get_object("tree_1").get_state("progress") == "2"
