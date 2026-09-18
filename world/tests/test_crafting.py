"""Tests for crafting recipes."""

import pytest

from world.crafting import RECIPES, process_craft_phase, station_nearby
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


def _add_station(
    world: World, position: Position, station: str, object_id: str = ""
) -> WorldObject:
    obj = WorldObject(
        object_id=object_id or f"{station}_1",
        position=position,
        object_type=station,
    )
    world.add_object(obj)
    return obj


def _add_workshop(world: World, position: Position) -> None:
    _add_station(world, position, "workshop_table", "workshop_1")


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
    "recipe,inputs,output_count,station",
    [
        ("plank", {"wood": 1}, 2, ""),
        ("rope", {"fiber": 2}, 1, ""),
        ("road", {"stone": 2}, 4, ""),
        ("wood_wall", {"plank": 2}, 1, ""),
        ("wood_floor", {"plank": 1}, 2, ""),
        ("workshop_table", {"plank": 4, "stone": 2}, 1, ""),
        ("stone_wall", {"stone": 2, "clay": 1}, 1, "workshop_table"),
        ("stone_floor", {"stone": 1, "clay": 1}, 2, "workshop_table"),
        ("door", {"plank": 3, "rope": 1}, 1, "workshop_table"),
        ("bed", {"plank": 4, "fiber": 3}, 1, "workshop_table"),
        ("chair", {"plank": 2}, 1, "workshop_table"),
        ("table", {"plank": 4}, 1, "workshop_table"),
    ],
)
def test_building_recipes_match_the_contract(
    recipe: str, inputs: dict[str, int], output_count: int, station: str
) -> None:
    entry = RECIPES[recipe]
    assert dict(entry.inputs) == inputs
    assert entry.output_count == output_count
    assert entry.station == station


@pytest.mark.parametrize("recipe", sorted(RECIPES))
def test_crafting_consumes_inputs_and_yields_output(recipe: str) -> None:
    entry = RECIPES[recipe]
    world = _crafter(**dict(entry.inputs))
    if entry.station:
        _add_station(world, CRAFTER_POSITION, entry.station)

    events = TickEvents()
    for _ in range(entry.work):
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

    def test_station_nearby_counts_the_crafters_own_tile(self) -> None:
        world = _crafter()
        _add_workshop(world, CRAFTER_POSITION)

        assert station_nearby(world, CRAFTER_POSITION, "workshop_table")

    def test_station_nearby_is_false_without_a_table(self) -> None:
        world = _crafter()

        assert not station_nearby(world, CRAFTER_POSITION, "workshop_table")


def test_multi_output_recipe_reports_the_count() -> None:
    world = _crafter(wood=1)

    events = _craft(world, "plank")

    assert events.action_results[0].details == "crafted plank x2"
    assert world.get_entity("bob").inventory.count("plank") == 2


class TestMultiTickCrafting:
    """docs/10_metal_and_sleep.md section 1: station recipes with work > 1."""

    def _furnace_crafter(self, **items: int) -> tuple[World, WorldObject]:
        world = _crafter(**items)
        furnace = _add_station(world, CRAFTER_POSITION, "furnace")
        return world, furnace

    def test_progress_is_reported_and_stored_on_the_station(self) -> None:
        world, furnace = self._furnace_crafter(wood=3)

        events = _craft(world, "charcoal")

        assert events.action_results[0].details == "charcoal 1/2"
        assert (
            world.get_object(furnace.object_id).get_state("craft:bob") == "charcoal:1"
        )

    def test_inputs_are_not_consumed_before_completion(self) -> None:
        world, _ = self._furnace_crafter(wood=3)

        _craft(world, "charcoal")

        assert world.get_entity("bob").inventory.count("wood") == 3
        assert world.get_entity("bob").inventory.count("charcoal") == 0

    def test_completion_consumes_inputs_and_clears_progress(self) -> None:
        world, furnace = self._furnace_crafter(wood=3)

        _craft(world, "charcoal")
        events = _craft(world, "charcoal")

        assert events.action_results[0].details == "crafted charcoal x2"
        bob = world.get_entity("bob")
        assert bob.inventory.count("charcoal") == 2
        assert bob.inventory.count("wood") == 0
        assert world.get_object(furnace.object_id).get_state("craft:bob") == ""

    def test_losing_the_inputs_mid_craft_fails_the_action(self) -> None:
        world, _ = self._furnace_crafter(wood=3)
        _craft(world, "charcoal")

        bob = world.get_entity("bob")
        world.set_entity(bob.with_inventory(bob.inventory.remove("wood", 3)))
        events = _craft(world, "charcoal")

        assert not events.action_results[0].success
        assert events.action_results[0].details == "charcoal needs 3 wood"

    def test_switching_recipe_resets_progress(self) -> None:
        world, furnace = self._furnace_crafter(wood=3, copper_ore=2, charcoal=1)

        _craft(world, "charcoal")
        events = _craft(world, "copper_ingot")

        assert events.action_results[0].details == "copper_ingot 1/3"
        assert (
            world.get_object(furnace.object_id).get_state("craft:bob")
            == "copper_ingot:1"
        )

    def test_progress_survives_walking_away_and_back(self) -> None:
        world, furnace = self._furnace_crafter(wood=3)
        _craft(world, "charcoal")

        bob = world.get_entity("bob")
        world.update_entity_position(bob.entity_id, Position(x=4, y=4))
        away = _craft(world, "charcoal")
        assert not away.action_results[0].success
        assert away.action_results[0].details == "charcoal needs a furnace nearby"

        world.update_entity_position(bob.entity_id, CRAFTER_POSITION)
        events = _craft(world, "charcoal")

        assert events.action_results[0].details == "crafted charcoal x2"
        assert world.get_object(furnace.object_id).get_state("craft:bob") == ""

    def test_progress_is_per_settler(self) -> None:
        world, furnace = self._furnace_crafter(wood=3)
        world.add_entity(
            Entity(
                entity_id="ann",
                position=Position(x=2, y=1),
                inventory=Inventory().add("wood", 3),
            )
        )
        events = TickEvents()
        process_craft_phase(
            world,
            {
                "bob": CraftIntent(entity_id="bob", recipe="charcoal"),
                "ann": CraftIntent(entity_id="ann", recipe="charcoal"),
            },
            events,
        )

        station = world.get_object(furnace.object_id)
        assert station.get_state("craft:bob") == "charcoal:1"
        assert station.get_state("craft:ann") == "charcoal:1"

    def test_progress_is_per_station(self) -> None:
        world, furnace = self._furnace_crafter(wood=6)
        other = _add_station(world, Position(x=2, y=2), "furnace", "furnace_2")
        _craft(world, "charcoal")

        world.remove_object(furnace.object_id)
        events = _craft(world, "charcoal")

        assert events.action_results[0].details == "charcoal 1/2"
        assert world.get_object(other.object_id).get_state("craft:bob") == "charcoal:1"

    def test_station_two_tiles_away_is_out_of_reach(self) -> None:
        world = _crafter(wood=3)
        _add_station(world, Position(x=3, y=1), "furnace")

        events = _craft(world, "charcoal")

        assert not events.action_results[0].success
        assert events.action_results[0].details == "charcoal needs a furnace nearby"

    def test_anvil_recipe_needs_an_anvil(self) -> None:
        world = _crafter(plank=1, iron_ingot=3)
        _add_workshop(world, CRAFTER_POSITION)

        events = _craft(world, "iron_sword")

        assert not events.action_results[0].success
        assert events.action_results[0].details == "iron_sword needs an anvil nearby"

    def test_iron_sword_completes_after_three_actions(self) -> None:
        world = _crafter(plank=1, iron_ingot=3)
        _add_station(world, CRAFTER_POSITION, "anvil")

        details = [
            _craft(world, "iron_sword").action_results[0].details for _ in range(3)
        ]

        assert details == ["iron_sword 1/3", "iron_sword 2/3", "crafted iron_sword"]
        assert world.get_entity("bob").inventory.count("iron_sword") == 1


@pytest.mark.parametrize(
    "recipe,inputs,output_count,station,work",
    [
        ("furnace", {"stone": 8, "clay": 4}, 1, "workshop_table", 3),
        ("charcoal", {"wood": 3}, 2, "furnace", 2),
        ("copper_ingot", {"copper_ore": 2, "charcoal": 1}, 1, "furnace", 3),
        ("iron_ingot", {"iron_ore": 2, "charcoal": 2}, 1, "furnace", 4),
        ("copper_axe", {"plank": 2, "copper_ingot": 2}, 1, "workshop_table", 2),
        ("copper_pickaxe", {"plank": 2, "copper_ingot": 2}, 1, "workshop_table", 2),
        ("anvil", {"iron_ingot": 5, "stone": 2}, 1, "workshop_table", 4),
        ("iron_axe", {"plank": 2, "iron_ingot": 2}, 1, "anvil", 2),
        ("iron_pickaxe", {"plank": 2, "iron_ingot": 2}, 1, "anvil", 2),
        ("iron_sword", {"plank": 1, "iron_ingot": 3}, 1, "anvil", 3),
    ],
)
def test_metal_recipes_match_the_contract(
    recipe: str, inputs: dict[str, int], output_count: int, station: str, work: int
) -> None:
    entry = RECIPES[recipe]
    assert dict(entry.inputs) == inputs
    assert entry.output_count == output_count
    assert entry.station == station
    assert entry.work == work


def test_hand_recipes_are_all_single_work() -> None:
    assert all(r.work == 1 for r in RECIPES.values() if not r.station)
