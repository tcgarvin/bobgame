"""Item kinds, object kinds, and the constants that relate them.

Single source of truth for what can be held, wielded, extracted, eaten and
placed. See docs/05_jev_agents_design.md ("Game mechanics").
"""

from typing import Mapping

# --- Inventory item kinds -------------------------------------------------

BERRY = "berry"
WOOD = "wood"
STONE = "stone"
AXE = "axe"
PICKAXE = "pickaxe"
SWORD = "sword"
CHEST = "chest"
MESSAGE_BOARD = "message_board"

ITEM_KINDS: frozenset[str] = frozenset(
    {BERRY, WOOD, STONE, AXE, PICKAXE, SWORD, CHEST, MESSAGE_BOARD}
)

# Items that can be placed in the world as an object.
PLACEABLE_KINDS: frozenset[str] = frozenset({CHEST, MESSAGE_BOARD})

# --- Combat ---------------------------------------------------------------

BASE_ATTACK_DAMAGE: Mapping[str, int] = {"wolf": 3}
DEFAULT_ATTACK_DAMAGE = 2

WIELD_DAMAGE_BONUS: Mapping[str, int] = {SWORD: 3, AXE: 2, PICKAXE: 1}

# --- Food -----------------------------------------------------------------

# kind -> hunger restored per unit eaten
FOOD_HUNGER_RESTORE: Mapping[str, int] = {BERRY: 20}

# --- Object kinds ---------------------------------------------------------

TREE = "tree"
BUSH = "bush"
ITEM_PILE = "item_pile"
CHEST_OBJECT = "chest"
MESSAGE_BOARD_OBJECT = "message_board"

ROCK_TYPES: frozenset[str] = frozenset(
    {"rock_small", "rock_medium", "rock_large", "boulder"}
)
EXTRACTABLE_TYPES: frozenset[str] = frozenset({TREE}) | ROCK_TYPES

# object_type -> units of material the untouched object holds
DEFAULT_REMAINING: Mapping[str, int] = {
    TREE: 4,
    "rock_small": 1,
    "rock_medium": 2,
    "rock_large": 4,
    "boulder": 6,
}

# object_type -> item kind yielded per extracted unit
EXTRACT_YIELD: Mapping[str, str] = {
    TREE: WOOD,
    "rock_small": STONE,
    "rock_medium": STONE,
    "rock_large": STONE,
    "boulder": STONE,
}

# object_type -> the wielded tool that speeds extraction up
EXTRACT_TOOL: Mapping[str, str] = {
    TREE: AXE,
    "rock_small": PICKAXE,
    "rock_medium": PICKAXE,
    "rock_large": PICKAXE,
    "boulder": PICKAXE,
}

# Work units needed for one unit of material.
EXTRACT_THRESHOLD = 3
EXTRACT_WORK_BARE = 1
EXTRACT_WORK_WITH_TOOL = 3

# Item kind placed by a PlaceIntent -> object_type created.
PLACED_OBJECT_TYPE: Mapping[str, str] = {
    CHEST: CHEST_OBJECT,
    MESSAGE_BOARD: MESSAGE_BOARD_OBJECT,
}


def attack_damage(entity_type: str, wielded: str) -> int:
    """Damage one attack of this entity type wielding `wielded` deals."""
    base = BASE_ATTACK_DAMAGE.get(entity_type, DEFAULT_ATTACK_DAMAGE)
    return base + WIELD_DAMAGE_BONUS.get(wielded, 0)


def default_remaining(object_type: str) -> int:
    """Units of material an untouched object of this type holds."""
    return DEFAULT_REMAINING.get(object_type, 0)
