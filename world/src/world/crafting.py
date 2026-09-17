"""Crafting recipes and the craft phase."""

from typing import Mapping

import structlog

from .events import TickEvents
from .items import AXE, CHEST, MESSAGE_BOARD, PICKAXE, STONE, SWORD, WOOD
from .state import World
from .types import CraftIntent

logger = structlog.get_logger()

# recipe name -> required (kind, count) inputs. The recipe name is the item
# kind produced (one unit).
RECIPES: Mapping[str, tuple[tuple[str, int], ...]] = {
    AXE: ((WOOD, 2), (STONE, 1)),
    PICKAXE: ((WOOD, 2), (STONE, 2)),
    SWORD: ((WOOD, 1), (STONE, 3)),
    CHEST: ((WOOD, 6),),
    MESSAGE_BOARD: ((WOOD, 4), (STONE, 1)),
}


def recipe_cost_text(recipe: str) -> str:
    """Human-readable input list for a recipe, e.g. "2 wood + 1 stone"."""
    return " + ".join(f"{count} {kind}" for kind, count in RECIPES[recipe])


def process_craft_phase(
    world: World,
    intents: Mapping[str, CraftIntent],
    events: TickEvents,
) -> None:
    """Craft recipes instantly, consuming inputs from the entity's inventory."""
    for entity_id in sorted(intents):
        intent = intents[entity_id]
        entity = world.get_entity(entity_id)
        inputs = RECIPES.get(intent.recipe)

        if inputs is None:
            events.acted(entity_id, "craft", False, f"unknown recipe {intent.recipe}")
            continue

        missing = [
            f"{count} {kind}"
            for kind, count in inputs
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

        inventory = entity.inventory
        for kind, count in inputs:
            inventory = inventory.remove(kind, count)
        inventory = inventory.add(intent.recipe, 1)
        world.set_entity(entity.with_inventory(inventory))

        events.acted(entity_id, "craft", True, f"crafted {intent.recipe}")
        logger.debug("craft_success", entity_id=entity_id, recipe=intent.recipe)
