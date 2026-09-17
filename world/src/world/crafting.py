"""Crafting recipes and the craft phase.

The recipe table mirrors docs/08_building.md; keep the two in step.
"""

from dataclasses import dataclass
from typing import Mapping

import structlog

from .events import TickEvents
from .items import (
    AXE,
    BED,
    CHAIR,
    CHEST,
    CLAY,
    DOOR,
    FIBER,
    MESSAGE_BOARD,
    PICKAXE,
    PLANK,
    ROAD,
    ROPE,
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
from .state import World
from .types import CraftIntent, Position, is_same_or_adjacent

logger = structlog.get_logger()


@dataclass(frozen=True)
class Recipe:
    """One craftable item: what it costs, how many it yields, where it works."""

    inputs: tuple[tuple[str, int], ...]
    output_count: int = 1
    needs_workshop: bool = False


# recipe name -> Recipe. The recipe name is the item kind produced.
RECIPES: Mapping[str, Recipe] = {
    AXE: Recipe(inputs=((WOOD, 2), (STONE, 1))),
    PICKAXE: Recipe(inputs=((WOOD, 2), (STONE, 2))),
    SWORD: Recipe(inputs=((WOOD, 1), (STONE, 3))),
    CHEST: Recipe(inputs=((WOOD, 6),)),
    MESSAGE_BOARD: Recipe(inputs=((WOOD, 4), (STONE, 1))),
    PLANK: Recipe(inputs=((WOOD, 1),), output_count=2),
    ROPE: Recipe(inputs=((FIBER, 2),)),
    ROAD: Recipe(inputs=((STONE, 2),), output_count=4),
    WOOD_WALL: Recipe(inputs=((PLANK, 2),)),
    WOOD_FLOOR: Recipe(inputs=((PLANK, 1),), output_count=2),
    WORKSHOP_TABLE: Recipe(inputs=((PLANK, 4), (STONE, 2))),
    STONE_WALL: Recipe(inputs=((STONE, 2), (CLAY, 1)), needs_workshop=True),
    STONE_FLOOR: Recipe(
        inputs=((STONE, 1), (CLAY, 1)), output_count=2, needs_workshop=True
    ),
    DOOR: Recipe(inputs=((PLANK, 3), (ROPE, 1)), needs_workshop=True),
    BED: Recipe(inputs=((PLANK, 4), (FIBER, 3)), needs_workshop=True),
    CHAIR: Recipe(inputs=((PLANK, 2),), needs_workshop=True),
    TABLE: Recipe(inputs=((PLANK, 4),), needs_workshop=True),
}


def recipe_cost_text(recipe: str) -> str:
    """Human-readable input list for a recipe, e.g. "2 wood + 1 stone"."""
    return " + ".join(f"{count} {kind}" for kind, count in RECIPES[recipe].inputs)


def workshop_nearby(world: World, position: Position) -> bool:
    """Whether a placed workshop table stands on or next to `position`.

    Scans the 9 tiles of the neighbourhood rather than every object in the
    world: the island map holds hundreds of thousands of objects.
    """
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            neighbour = Position(x=position.x + dx, y=position.y + dy)
            for obj in world.get_objects_at(neighbour):
                if obj.object_type == WORKSHOP_TABLE:
                    return True
    return False


def process_craft_phase(
    world: World,
    intents: Mapping[str, CraftIntent],
    events: TickEvents,
) -> None:
    """Craft recipes instantly, consuming inputs from the entity's inventory."""
    for entity_id in sorted(intents):
        intent = intents[entity_id]
        entity = world.get_entity(entity_id)
        recipe = RECIPES.get(intent.recipe)

        if recipe is None:
            events.acted(entity_id, "craft", False, f"unknown recipe {intent.recipe}")
            continue

        missing = [
            f"{count} {kind}"
            for kind, count in recipe.inputs
            if not entity.inventory.has(kind, count)
        ]
        if missing:
            events.acted(
                entity_id,
                "craft",
                False,
                f"{intent.recipe} needs {recipe_cost_text(intent.recipe)}",
            )
            continue

        if recipe.needs_workshop and not workshop_nearby(world, entity.position):
            events.acted(
                entity_id,
                "craft",
                False,
                f"{intent.recipe} needs a workshop table nearby",
            )
            continue

        inventory = entity.inventory
        for kind, count in recipe.inputs:
            inventory = inventory.remove(kind, count)
        inventory = inventory.add(intent.recipe, recipe.output_count)
        world.set_entity(entity.with_inventory(inventory))

        # Detail keeps the historic "crafted <kind>" prefix that run analysis
        # greps for; multi-output recipes append the count.
        detail = f"crafted {intent.recipe}"
        if recipe.output_count > 1:
            detail = f"{detail} x{recipe.output_count}"
        events.acted(entity_id, "craft", True, detail)
        logger.debug("craft_success", entity_id=entity_id, recipe=intent.recipe)
