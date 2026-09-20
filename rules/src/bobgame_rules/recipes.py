"""The crafting recipe table (docs/10_metal_and_sleep.md)."""

from dataclasses import dataclass
from typing import Mapping

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

# Prefix of the per-settler progress key kept in a station object's state,
# as `"craft:<entity id>" -> "<recipe>:<actions done>"`.
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

    def cost_text(self) -> str:
        """`"2 wood + 1 stone"`, in the table's order."""
        return " + ".join(f"{count} {kind}" for kind, count in self.inputs)


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
    return RECIPES[recipe].cost_text()
