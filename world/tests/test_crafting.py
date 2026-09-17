"""Tests for crafting recipes."""

import pytest

from world.crafting import RECIPES, process_craft_phase
from world.events import TickEvents
from world.state import Entity, Inventory, World
from world.types import CraftIntent, Position


def _crafter(**items: int) -> World:
    world = World(width=5, height=5)
    inventory = Inventory()
    for kind, count in items.items():
        inventory = inventory.add(kind, count)
    world.add_entity(
        Entity(entity_id="bob", position=Position(x=1, y=1), inventory=inventory)
    )
    return world


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
    costs = dict(RECIPES[recipe])
    assert costs.get("wood", 0) == wood
    assert costs.get("stone", 0) == stone


@pytest.mark.parametrize("recipe", sorted(RECIPES))
def test_crafting_consumes_inputs_and_yields_output(recipe: str) -> None:
    costs = dict(RECIPES[recipe])
    world = _crafter(**costs)

    events = _craft(world, recipe)

    bob = world.get_entity("bob")
    assert events.action_results[0].success
    assert bob.inventory.count(recipe) == 1
    for kind in costs:
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
