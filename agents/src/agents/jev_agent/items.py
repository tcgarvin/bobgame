"""The agent-side mirror of the world's item, recipe and object tables.

The agents package cannot import `world`, so this module restates the contract
in `world/src/world/items.py` and `docs/08_building.md`. Keep the two in step:
if a recipe or a kind changes there, change it here in the same commit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

# --- Raw and intermediate materials ---------------------------------------

BERRY = "berry"
WOOD = "wood"
STONE = "stone"
FIBER = "fiber"
CLAY = "clay"
PLANK = "plank"
ROPE = "rope"

AXE = "axe"
PICKAXE = "pickaxe"
SWORD = "sword"
CHEST = "chest"
MESSAGE_BOARD = "message_board"

# --- Building items (the placed object's type equals the item kind) --------

ROAD = "road"
WOOD_FLOOR = "wood_floor"
STONE_FLOOR = "stone_floor"
WOOD_WALL = "wood_wall"
STONE_WALL = "stone_wall"
DOOR = "door"
BED = "bed"
CHAIR = "chair"
TABLE = "table"
WORKSHOP_TABLE = "workshop_table"

# Ground-layer kinds lie under structures, never block, and are placed on the
# placer's own tile with no direction.
GROUND_LAYER_KINDS: frozenset[str] = frozenset({ROAD, WOOD_FLOOR, STONE_FLOOR})

BUILDING_KINDS: frozenset[str] = GROUND_LAYER_KINDS | frozenset(
    {WOOD_WALL, STONE_WALL, DOOR, BED, CHAIR, TABLE, WORKSHOP_TABLE}
)

PLACEABLE_KINDS: frozenset[str] = frozenset({CHEST, MESSAGE_BOARD}) | BUILDING_KINDS

WIELDABLE_KINDS: frozenset[str] = frozenset({AXE, PICKAXE, SWORD})

# --- Natural objects -------------------------------------------------------

TREE = "tree"
BUSH = "bush"
REEDS = "reeds"
CLAY_DEPOSIT = "clay_deposit"
ITEM_PILE = "item_pile"

ROCK_TYPES: frozenset[str] = frozenset(
    {"rock_small", "rock_medium", "rock_large", "boulder"}
)
EXTRACTABLE_TYPES: frozenset[str] = frozenset({TREE, REEDS, CLAY_DEPOSIT}) | ROCK_TYPES

# Ground-layer items may not be placed on a tile holding one of these.
NATURAL_OBJECT_TYPES: frozenset[str] = EXTRACTABLE_TYPES | frozenset({BUSH})

# Object types nobody may walk through. A door blocks wolves only, so the agent
# treats it as walkable.
BLOCKING_OBJECT_TYPES: frozenset[str] = frozenset({WOOD_WALL, STONE_WALL})

DEFAULT_REMAINING: Mapping[str, int] = {
    TREE: 4,
    "rock_small": 1,
    "rock_medium": 2,
    "rock_large": 4,
    "boulder": 6,
    REEDS: 3,
    CLAY_DEPOSIT: 6,
}

# object_type -> the item one unit of extraction yields.
EXTRACT_YIELD: Mapping[str, str] = {
    TREE: WOOD,
    "rock_small": STONE,
    "rock_medium": STONE,
    "rock_large": STONE,
    "boulder": STONE,
    REEDS: FIBER,
    CLAY_DEPOSIT: CLAY,
}

# object_type -> the wielded tool that triples extraction speed ("" for none).
EXTRACT_TOOL: Mapping[str, str] = {
    TREE: AXE,
    "rock_small": PICKAXE,
    "rock_medium": PICKAXE,
    "rock_large": PICKAXE,
    "boulder": PICKAXE,
    CLAY_DEPOSIT: PICKAXE,
}

# Extract actions needed to dismantle one placed building object.
DISMANTLE_WORK = 3

# Health one successful rest on a bed restores.
REST_HEAL = 2

# --- Combat and speech (mirrors world/items.py, world/wolves.py) -------------

PLAYER_MAX_HEALTH = 20
UNARMED_DAMAGE = 2
WIELD_DAMAGE_BONUS: Mapping[str, int] = {SWORD: 3, AXE: 2, PICKAXE: 1}
WOLF_HEALTH = 16
WOLF_DAMAGE = 3

LOCAL_CHANNEL = "local"
SHOUT_CHANNEL = "shout"
SAY_RADIUS = 10
SHOUT_RADIUS = 60

# --- Conversations (mirrors world/items.py, docs/09) -------------------------

CONVERSATION = "conversation"
CONVERSATION_CHANNEL = "conversation"
CONVERSATION_MAX_PARTICIPANTS = 4
# A turn nobody used within this many ticks counts as a pass.
CONVERSATION_TURN_TICKS = 10
# A conversation with only its opener closes after this many ticks.
CONVERSATION_LONELY_TICKS = 20
CONVERSATION_MAX_UTTERANCES = 40
CONVERSATION_TEXT_LIMIT = 300
CONVERSATION_TRANSCRIPT_KEPT = 12


# --- Recipes ---------------------------------------------------------------


@dataclass(frozen=True)
class Recipe:
    """One craftable item: what it eats, how many it yields, where it works."""

    inputs: Mapping[str, int]
    output_count: int = 1
    needs_workshop: bool = False

    def cost_text(self) -> str:
        """`"2 wood + 1 stone"`, in the table's order."""
        return " + ".join(f"{amount} {kind}" for kind, amount in self.inputs.items())


RECIPES: Mapping[str, Recipe] = {
    AXE: Recipe({WOOD: 2, STONE: 1}),
    PICKAXE: Recipe({WOOD: 2, STONE: 2}),
    SWORD: Recipe({WOOD: 1, STONE: 3}),
    CHEST: Recipe({WOOD: 6}),
    MESSAGE_BOARD: Recipe({WOOD: 4, STONE: 1}),
    PLANK: Recipe({WOOD: 1}, output_count=2),
    ROPE: Recipe({FIBER: 2}),
    ROAD: Recipe({STONE: 2}, output_count=4),
    WOOD_WALL: Recipe({PLANK: 2}),
    WOOD_FLOOR: Recipe({PLANK: 1}, output_count=2),
    WORKSHOP_TABLE: Recipe({PLANK: 4, STONE: 2}),
    STONE_WALL: Recipe({STONE: 2, CLAY: 1}, needs_workshop=True),
    STONE_FLOOR: Recipe({STONE: 1, CLAY: 1}, output_count=2, needs_workshop=True),
    DOOR: Recipe({PLANK: 3, ROPE: 1}, needs_workshop=True),
    BED: Recipe({PLANK: 4, FIBER: 3}, needs_workshop=True),
    CHAIR: Recipe({PLANK: 2}, needs_workshop=True),
    TABLE: Recipe({PLANK: 4}, needs_workshop=True),
}

# Legacy view used by callers that only care about the inputs.
CRAFT_RECIPES: Mapping[str, Mapping[str, int]] = {
    name: recipe.inputs for name, recipe in RECIPES.items()
}


def recipe_table_text() -> str:
    """The whole recipe table as prompt lines, one recipe per line."""
    lines: list[str] = []
    for name, recipe in RECIPES.items():
        yields = "" if recipe.output_count == 1 else f" (yields {recipe.output_count})"
        where = " [workshop table]" if recipe.needs_workshop else ""
        lines.append(f"  {name} = {recipe.cost_text()}{yields}{where}")
    return "\n".join(lines)


def is_ground_kind(kind: str) -> bool:
    """Whether this item is placed on the placer's own tile with no direction."""
    return kind in GROUND_LAYER_KINDS
