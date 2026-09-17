"""Tests for crafting recipes."""

import pytest

from world.crafting import RECIPES, process_craft_phase, workshop_nearby
from world.events import TickEvents
from world.state import Entity, Inventory, World, WorldObject
from world.types import CraftIntent, Position

CRAFTER_POSITION = Position(x=1, y=1)


def _crafter(**items: int) -> World:
    world = World(width=5, height=5)
    inventory = Inventory()
    for kind, count in items.items():
        inventory = inventory.add(kind, count)
    world.add_entity(
        Entity(entity_id="bob", position=CRAFTER_POSITION, inventory=inventory)
    )
    return world


def _add_workshop(world: World, position: Position) -> None:
    world.add_object(
        WorldObject(
            object_id="workshop_1", position=position, object_type="workshop_table"
        )
    )


def _craft(world: World, recipe: str) -> TickEvents:
    events = TickEvents()
    process_craft_phase(
        world, {"bob": CraftIntent(entity_id="bob", recipe=recipe)}, events
    )
    return events


@pytest.mark.parametrize(
    "recipe,wood,stone",
    [
        ("axe", 2, 1),
        ("pickaxe", 2, 2),
        ("sword", 1, 3),
        ("chest", 6, 0),
        ("message_board", 4, 1),
    ],
)
def test_recipe_costs_match_the_contract(recipe: str, wood: int, stone: int) -> None:
    costs = dict(RECIPES[recipe].inputs)
    assert costs.get("wood", 0) == wood
    assert costs.get("stone", 0) == stone


@pytest.mark.parametrize(
    "recipe,inputs,output_count,needs_workshop",
    [
        ("plank", {"wood": 1}, 2, False),
        ("rope", {"fiber": 2}, 1, False),
        ("road", {"stone": 2}, 4, False),
        ("wood_wall", {"plank": 2}, 1, False),
        ("wood_floor", {"plank": 1}, 2, False),
        ("workshop_table", {"plank": 4, "stone": 2}, 1, False),
        ("stone_wall", {"stone": 2, "clay": 1}, 1, True),
        ("stone_floor", {"stone": 1, "clay": 1}, 2, True),
        ("door", {"plank": 3, "rope": 1}, 1, True),
        ("bed", {"plank": 4, "fiber": 3}, 1, True),
        ("chair", {"plank": 2}, 1, True),
        ("table", {"plank": 4}, 1, True),
    ],
)
def test_building_recipes_match_the_contract(
    recipe: str, inputs: dict[str, int], output_count: int, needs_workshop: bool
) -> None:
    entry = RECIPES[recipe]
    assert dict(entry.inputs) == inputs
    assert entry.output_count == output_count
    assert entry.needs_workshop is needs_workshop


@pytest.mark.parametrize("recipe", sorted(RECIPES))
def test_crafting_consumes_inputs_and_yields_output(recipe: str) -> None:
    entry = RECIPES[recipe]
    world = _crafter(**dict(entry.inputs))
    if entry.needs_workshop:
        _add_workshop(world, CRAFTER_POSITION)

    events = _craft(world, recipe)

    bob = world.get_entity("bob")
    assert events.action_results[0].success
    assert bob.inventory.count(recipe) == entry.output_count
    for kind in dict(entry.inputs):
        assert bob.inventory.count(kind) == 0


def test_crafting_without_materials_fails() -> None:
    world = _crafter(wood=1)

    events = _craft(world, "axe")

    assert not events.action_results[0].success
    assert "2 wood + 1 stone" in events.action_results[0].details
    assert world.get_entity("bob").inventory.count("axe") == 0
    assert world.get_entity("bob").inventory.count("wood") == 1


def test_unknown_recipe_fails() -> None:
    world = _crafter(wood=10, stone=10)

    events = _craft(world, "castle")

    assert not events.action_results[0].success
    assert "unknown recipe" in events.action_results[0].details


class TestWorkshopGate:
    def test_workshop_recipe_without_a_table_fails(self) -> None:
        world = _crafter(plank=2)

        events = _craft(world, "chair")

        assert not events.action_results[0].success
        assert events.action_results[0].details == (
            "chair needs a workshop table nearby"
        )
        assert world.get_entity("bob").inventory.count("plank") == 2

    def test_workshop_recipe_succeeds_on_an_adjacent_table(self) -> None:
        world = _crafter(plank=2)
        _add_workshop(world, Position(x=2, y=2))

        events = _craft(world, "chair")

        assert events.action_results[0].success
        assert world.get_entity("bob").inventory.count("chair") == 1

    def test_workshop_two_tiles_away_is_out_of_reach(self) -> None:
        world = _crafter(plank=2)
        _add_workshop(world, Position(x=3, y=1))

        events = _craft(world, "chair")

        assert not events.action_results[0].success

    def test_hand_recipe_ignores_the_workshop(self) -> None:
        world = _crafter(wood=2, stone=1)

        events = _craft(world, "axe")

        assert events.action_results[0].success

    def test_workshop_nearby_counts_the_crafters_own_tile(self) -> None:
        world = _crafter()
        _add_workshop(world, CRAFTER_POSITION)

        assert workshop_nearby(world, CRAFTER_POSITION)

    def test_workshop_nearby_is_false_without_a_table(self) -> None:
        world = _crafter()

        assert not workshop_nearby(world, CRAFTER_POSITION)


def test_multi_output_recipe_reports_the_count() -> None:
    world = _crafter(wood=1)

    events = _craft(world, "plank")

    assert events.action_results[0].details == "crafted plank x2"
    assert world.get_entity("bob").inventory.count("plank") == 2
