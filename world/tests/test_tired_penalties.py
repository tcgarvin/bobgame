"""Tired settlers (fatigue >= 60) work slower and hit softer (docs/10)."""

from world.combat import process_attack_phase
from world.events import TickEvents
from world.foraging import process_extract_phase, tired_work
from world.items import AXE, SWORD, TREE, WOOD
from world.sleep import TIRED_FATIGUE
from world.state import Entity, World, WorldObject
from world.types import AttackIntent, ExtractIntent, Position


def _world_with_tree(fatigue: int) -> tuple[World, str]:
    world = World(width=10, height=10)
    entity = Entity(
        entity_id="ada",
        position=Position(x=1, y=1),
        entity_type="player",
        wielded=AXE,
        fatigue=fatigue,
    )
    world.add_entity(entity)
    tree = WorldObject(
        object_id="tree_1",
        position=Position(x=2, y=1),
        object_type=TREE,
        state=(("remaining", "4"),),
    )
    world.add_object(tree)
    return world, tree.object_id


def test_tired_work_halves_with_a_floor_of_one() -> None:
    assert tired_work(5) == 2
    assert tired_work(3) == 1
    assert tired_work(1) == 1


def test_fresh_axe_wielder_fells_a_unit_per_action() -> None:
    world, tree_id = _world_with_tree(fatigue=TIRED_FATIGUE - 1)
    events = TickEvents()
    process_extract_phase(
        world, {"ada": ExtractIntent(entity_id="ada", object_id=tree_id)}, events
    )
    assert world.get_entity("ada").inventory.count(WOOD) == 1


def test_tired_axe_wielder_needs_three_actions_per_unit() -> None:
    world, tree_id = _world_with_tree(fatigue=TIRED_FATIGUE)
    for _ in range(2):
        process_extract_phase(
            world,
            {"ada": ExtractIntent(entity_id="ada", object_id=tree_id)},
            TickEvents(),
        )
    assert world.get_entity("ada").inventory.count(WOOD) == 0
    process_extract_phase(
        world, {"ada": ExtractIntent(entity_id="ada", object_id=tree_id)}, TickEvents()
    )
    assert world.get_entity("ada").inventory.count(WOOD) == 1


def _duel(fatigue: int) -> int:
    world = World(width=10, height=10)
    world.add_entity(
        Entity(
            entity_id="ada",
            position=Position(x=1, y=1),
            entity_type="player",
            wielded=SWORD,
            fatigue=fatigue,
        )
    )
    world.add_entity(
        Entity(
            entity_id="wolf_1",
            position=Position(x=2, y=1),
            entity_type="wolf",
            health=16,
            max_health=16,
        )
    )
    events = TickEvents()
    process_attack_phase(
        world, {"ada": AttackIntent(entity_id="ada", target_entity_id="wolf_1")}, events
    )
    return 16 - world.get_entity("wolf_1").health


def test_fresh_swordsman_deals_five() -> None:
    assert _duel(fatigue=0) == 5


def test_tired_swordsman_deals_four() -> None:
    assert _duel(fatigue=TIRED_FATIGUE) == 4
