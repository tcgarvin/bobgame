"""Crafting recipes and the craft phase.

The recipe table mirrors docs/10_metal_and_sleep.md (which supersedes the table
in docs/08_building.md); keep the two in step.
"""

from dataclasses import dataclass
from typing import Mapping

import structlog

from .events import TickEvents, commit_object
from .items import (
    ANVIL,
    AXE,
    BED,
    CHAIR,
    CHARCOAL,
    CHEST,
    CLAY,
    COPPER_AXE,
    COPPER_INGOT,
    COPPER_ORE,
    COPPER_PICKAXE,
    DOOR,
    FIBER,
    FURNACE,
    IRON_AXE,
    IRON_INGOT,
    IRON_ORE,
    IRON_PICKAXE,
    IRON_SWORD,
    MESSAGE_BOARD,
    PICKAXE,
    PLANK,
    ROAD,
    ROPE,
    SIGN,
    STONE,
    STONE_FLOOR,
    STONE_WALL,
    SWORD,
    TABLE,
    WOOD,
    WOOD_FLOOR,
    WOOD_WALL,
    WORKSHOP_TABLE,
)
from .state import World, WorldObject
from .types import CraftIntent, Position

logger = structlog.get_logger()

# Prefix of the per-settler progress key kept in a station object's state.
CRAFT_PROGRESS_PREFIX = "craft:"


@dataclass(frozen=True)
class Recipe:
    """One craftable item: what it costs, how many it yields, where it works.

    `station` is "" for hand crafting, otherwise the object type that must be
    on or next to the crafter. `work` is the number of craft actions needed;
    hand recipes are always 1 and complete instantly.
    """

    inputs: tuple[tuple[str, int], ...]
    output_count: int = 1
    station: str = ""
    work: int = 1


# recipe name -> Recipe. The recipe name is the item kind produced.
RECIPES: Mapping[str, Recipe] = {
    AXE: Recipe(inputs=((WOOD, 2), (STONE, 1))),
    PICKAXE: Recipe(inputs=((WOOD, 2), (STONE, 2))),
    SWORD: Recipe(inputs=((WOOD, 1), (STONE, 3))),
    CHEST: Recipe(inputs=((WOOD, 6),)),
    MESSAGE_BOARD: Recipe(inputs=((WOOD, 4), (STONE, 1))),
    SIGN: Recipe(inputs=((WOOD, 2),)),
    PLANK: Recipe(inputs=((WOOD, 1),), output_count=2),
    ROPE: Recipe(inputs=((FIBER, 2),)),
    ROAD: Recipe(inputs=((STONE, 2),), output_count=4),
    WOOD_WALL: Recipe(inputs=((PLANK, 2),)),
    WOOD_FLOOR: Recipe(inputs=((PLANK, 1),), output_count=2),
    WORKSHOP_TABLE: Recipe(inputs=((PLANK, 4), (STONE, 2))),
    STONE_WALL: Recipe(inputs=((STONE, 2), (CLAY, 1)), station=WORKSHOP_TABLE),
    STONE_FLOOR: Recipe(
        inputs=((STONE, 1), (CLAY, 1)), output_count=2, station=WORKSHOP_TABLE
    ),
    DOOR: Recipe(inputs=((PLANK, 3), (ROPE, 1)), station=WORKSHOP_TABLE),
    BED: Recipe(inputs=((PLANK, 4), (FIBER, 3)), station=WORKSHOP_TABLE),
    CHAIR: Recipe(inputs=((PLANK, 2),), station=WORKSHOP_TABLE),
    TABLE: Recipe(inputs=((PLANK, 4),), station=WORKSHOP_TABLE),
    FURNACE: Recipe(inputs=((STONE, 8), (CLAY, 4)), station=WORKSHOP_TABLE, work=3),
    CHARCOAL: Recipe(inputs=((WOOD, 3),), output_count=2, station=FURNACE, work=2),
    COPPER_INGOT: Recipe(
        inputs=((COPPER_ORE, 2), (CHARCOAL, 1)), station=FURNACE, work=3
    ),
    IRON_INGOT: Recipe(inputs=((IRON_ORE, 2), (CHARCOAL, 2)), station=FURNACE, work=4),
    COPPER_AXE: Recipe(
        inputs=((PLANK, 2), (COPPER_INGOT, 2)), station=WORKSHOP_TABLE, work=2
    ),
    COPPER_PICKAXE: Recipe(
        inputs=((PLANK, 2), (COPPER_INGOT, 2)), station=WORKSHOP_TABLE, work=2
    ),
    ANVIL: Recipe(inputs=((IRON_INGOT, 5), (STONE, 2)), station=WORKSHOP_TABLE, work=4),
    IRON_AXE: Recipe(inputs=((PLANK, 2), (IRON_INGOT, 2)), station=ANVIL, work=2),
    IRON_PICKAXE: Recipe(inputs=((PLANK, 2), (IRON_INGOT, 2)), station=ANVIL, work=2),
    IRON_SWORD: Recipe(inputs=((PLANK, 1), (IRON_INGOT, 3)), station=ANVIL, work=3),
}


def recipe_cost_text(recipe: str) -> str:
    """Human-readable input list for a recipe, e.g. "2 wood + 1 stone"."""
    return " + ".join(f"{count} {kind}" for kind, count in RECIPES[recipe].inputs)


def nearest_station(world: World, position: Position, station: str) -> WorldObject:
    """Return the station of this type on or next to `position`.

    Scans the 9 tiles of the neighbourhood rather than every object in the
    world: the island map holds hundreds of thousands of objects. The crafter's
    own tile comes first, then the neighbours in a fixed order; ties within a
    tile go to the smallest object id, so the choice is deterministic.

    Raises:
        LookupError: when no such station stands in the neighbourhood.
    """
    offsets = [(0, 0)] + [
        (dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (dx, dy) != (0, 0)
    ]
    for dx, dy in offsets:
        neighbour = Position(x=position.x + dx, y=position.y + dy)
        matches = [
            obj for obj in world.get_objects_at(neighbour) if obj.object_type == station
        ]
        if matches:
            return min(matches, key=lambda obj: obj.object_id)
    raise LookupError(f"no {station} on or next to {position}")


def station_nearby(world: World, position: Position, station: str) -> bool:
    """Whether a placed station of this type stands on or next to `position`."""
    try:
        nearest_station(world, position, station)
    except LookupError:
        return False
    return True


def _station_label(station: str) -> str:
    """Station object type with its article, as it reads in a failure detail."""
    label = station.replace("_", " ")
    article = "an" if label[0] in "aeiou" else "a"
    return f"{article} {label}"


def _progress_key(entity_id: str) -> str:
    """State key holding one settler's craft progress on a station."""
    return f"{CRAFT_PROGRESS_PREFIX}{entity_id}"


def _read_progress(obj: WorldObject, entity_id: str, recipe: str) -> int:
    """Craft actions this settler has already put into `recipe` at `obj`.

    Progress on a different recipe counts as zero: switching recipe resets.
    """
    raw = obj.get_state(_progress_key(entity_id), "")
    if not raw:
        return 0
    stored_recipe, _, done = raw.rpartition(":")
    if stored_recipe != recipe:
        return 0
    if not done.isdigit():
        logger.warning("craft_progress_unreadable", object_id=obj.object_id, raw=raw)
        return 0
    return int(done)


def _without_state(obj: WorldObject, key: str) -> WorldObject:
    """Return a copy of `obj` with `key` removed from its state."""
    state = tuple((k, v) for k, v in obj.state if k != key)
    return obj.model_copy(update={"state": state})


def _write_progress(
    world: World,
    obj: WorldObject,
    entity_id: str,
    value: str,
    events: TickEvents,
) -> None:
    """Store (or clear, when `value` is empty) a settler's progress on a station."""
    key = _progress_key(entity_id)
    updated = _without_state(obj, key) if value == "" else obj.with_state(key, value)
    commit_object(world, obj, updated, events)


def process_craft_phase(
    world: World,
    intents: Mapping[str, CraftIntent],
    events: TickEvents,
) -> None:
    """Apply one craft action per intent.

    Hand recipes and one-work station recipes complete at once; a station
    recipe with `work > 1` accumulates progress on the station object and only
    consumes the inputs on the action that completes it (docs/10, section 1).
    """
    for entity_id in sorted(intents):
        _craft_once(world, entity_id, intents[entity_id].recipe, events)


def _craft_once(world: World, entity_id: str, name: str, events: TickEvents) -> None:
    """Run one craft action for one settler."""
    entity = world.get_entity(entity_id)
    recipe = RECIPES.get(name)

    if recipe is None:
        events.acted(entity_id, "craft", False, f"unknown recipe {name}")
        return

    missing = [
        kind for kind, count in recipe.inputs if not entity.inventory.has(kind, count)
    ]
    if missing:
        events.acted(
            entity_id, "craft", False, f"{name} needs {recipe_cost_text(name)}"
        )
        return

    station_obj: WorldObject | None = None
    if recipe.station:
        try:
            station_obj = nearest_station(world, entity.position, recipe.station)
        except LookupError:
            events.acted(
                entity_id,
                "craft",
                False,
                f"{name} needs {_station_label(recipe.station)} nearby",
            )
            return

    done = 1
    if station_obj is not None and recipe.work > 1:
        done = _read_progress(station_obj, entity_id, name) + 1
        if done < recipe.work:
            _write_progress(world, station_obj, entity_id, f"{name}:{done}", events)
            events.acted(entity_id, "craft", True, f"{name} {done}/{recipe.work}")
            return

    inventory = entity.inventory
    for kind, count in recipe.inputs:
        inventory = inventory.remove(kind, count)
    inventory = inventory.add(name, recipe.output_count)
    world.set_entity(entity.with_inventory(inventory))

    if station_obj is not None and recipe.work > 1:
        _write_progress(world, station_obj, entity_id, "", events)

    # Detail keeps the historic "crafted <kind>" prefix that run analysis
    # greps for; multi-output recipes append the count.
    detail = f"crafted {name}"
    if recipe.output_count > 1:
        detail = f"{detail} x{recipe.output_count}"
    events.acted(entity_id, "craft", True, detail)
    logger.debug("craft_success", entity_id=entity_id, recipe=name)
