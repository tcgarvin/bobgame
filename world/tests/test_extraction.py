"""Tests for tree chopping, rock mining, reed cutting and clay digging."""

import pytest

from world.events import TickEvents
from world.foraging import extract_work, process_extract_phase
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
        assert default_remaining("reeds") == 3
        assert default_remaining("clay_deposit") == 6


class TestNewNaturalResources:
    @pytest.mark.parametrize(
        "object_type,yielded,tool",
        [("reeds", "fiber", ""), ("clay_deposit", "clay", "pickaxe")],
    )
    def test_extracting_yields_its_material(
        self, object_type: str, yielded: str, tool: str
    ) -> None:
        world = World(width=10, height=10)
        world.add_entity(
            Entity(
                entity_id="bob",
                position=Position(x=5, y=5),
                inventory=Inventory().add("pickaxe", 1),
                wielded=tool,
            )
        )
        world.add_object(
            WorldObject(
                object_id="source_1",
                position=Position(x=6, y=5),
                object_type=object_type,
            )
        )
        events = TickEvents()
        process_extract_phase(
            world,
            {"bob": ExtractIntent(entity_id="bob", object_id="source_1")},
            events,
        )

        assert events.action_results[0].success
        # A matching tool finishes one unit in a single action; bare hands do not.
        expected = 1 if tool else 0
        assert world.get_entity("bob").inventory.count(yielded) == expected

    def test_reeds_do_not_regrow(self) -> None:
        world = World(width=10, height=10)
        world.add_entity(Entity(entity_id="bob", position=Position(x=5, y=5)))
        world.add_object(
            WorldObject(
                object_id="reeds_1",
                position=Position(x=6, y=5),
                object_type="reeds",
                state=(("remaining", "1"), ("progress", "2")),
            )
        )
        events = TickEvents()
        process_extract_phase(
            world, {"bob": ExtractIntent(entity_id="bob", object_id="reeds_1")}, events
        )

        assert "reeds_1" not in world.all_objects()
        assert [e.object_id for e in events.objects_removed] == ["reeds_1"]


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


class TestOreVeins:
    """docs/10_metal_and_sleep.md section 2: veins, tool gating and tiers."""

    def _vein_world(self, object_type: str, wielded: str) -> World:
        world = World(width=10, height=10)
        world.add_entity(
            Entity(
                entity_id="bob",
                position=Position(x=5, y=5),
                inventory=Inventory().add(wielded, 1) if wielded else Inventory(),
                wielded=wielded,
            )
        )
        world.add_object(
            WorldObject(
                object_id="vein_1", position=Position(x=6, y=5), object_type=object_type
            )
        )
        return world

    def _work(self, world: World) -> TickEvents:
        events = TickEvents()
        process_extract_phase(
            world,
            {"bob": ExtractIntent(entity_id="bob", object_id="vein_1")},
            events,
        )
        return events

    def test_vein_defaults_hold_four_units(self) -> None:
        assert default_remaining("copper_vein") == 4
        assert default_remaining("iron_vein") == 4

    @pytest.mark.parametrize(
        "object_type,tools",
        [
            ("copper_vein", "copper_pickaxe or iron_pickaxe or pickaxe"),
            ("iron_vein", "copper_pickaxe or iron_pickaxe"),
        ],
    )
    def test_bare_hands_fail_with_the_tool_list(
        self, object_type: str, tools: str
    ) -> None:
        world = self._vein_world(object_type, "")

        events = self._work(world)

        assert not events.action_results[0].success
        assert events.action_results[0].details == f"vein_1 needs a {tools}"

    def test_a_plain_pickaxe_cannot_work_an_iron_vein(self) -> None:
        world = self._vein_world("iron_vein", "pickaxe")

        events = self._work(world)

        assert not events.action_results[0].success
        assert world.get_entity("bob").inventory.count("iron_ore") == 0

    @pytest.mark.parametrize(
        "object_type,tool,yielded",
        [
            ("copper_vein", "pickaxe", "copper_ore"),
            ("copper_vein", "copper_pickaxe", "copper_ore"),
            ("iron_vein", "copper_pickaxe", "iron_ore"),
            ("iron_vein", "iron_pickaxe", "iron_ore"),
        ],
    )
    def test_an_acceptable_pickaxe_yields_ore_in_one_action(
        self, object_type: str, tool: str, yielded: str
    ) -> None:
        world = self._vein_world(object_type, tool)

        events = self._work(world)

        assert events.action_results[0].success
        assert world.get_entity("bob").inventory.count(yielded) == 1

    def test_an_axe_does_not_open_a_vein(self) -> None:
        world = self._vein_world("copper_vein", "iron_axe")

        events = self._work(world)

        assert not events.action_results[0].success


class TestToolTiers:
    @pytest.mark.parametrize(
        "tool,work",
        [
            ("", 1),
            ("axe", 3),
            ("copper_axe", 4),
            ("iron_axe", 5),
            ("pickaxe", 1),
            ("sword", 1),
        ],
    )
    def test_work_per_action_on_a_tree(self, tool: str, work: int) -> None:
        assert extract_work(tool, "tree") == work

    @pytest.mark.parametrize(
        "tool,work",
        [
            ("", 1),
            ("pickaxe", 3),
            ("copper_pickaxe", 4),
            ("iron_pickaxe", 5),
            ("axe", 1),
        ],
    )
    def test_work_per_action_on_a_boulder(self, tool: str, work: int) -> None:
        assert extract_work(tool, "boulder") == work

    def test_an_iron_axe_takes_a_tree_unit_every_action(self) -> None:
        world = _world_with_tree("iron_axe")

        _extract(world, "bob")

        assert world.get_entity("bob").inventory.count("wood") == 1
