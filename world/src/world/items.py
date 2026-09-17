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

# Raw and intermediate building materials (docs/08_building.md).
FIBER = "fiber"
CLAY = "clay"
PLANK = "plank"
ROPE = "rope"

# Building items. The placed object's object_type equals the item kind.
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

# Ground-layer kinds lie under structures and never block.
GROUND_LAYER_KINDS: frozenset[str] = frozenset({ROAD, WOOD_FLOOR, STONE_FLOOR})

# Everything the building update added; all of it can be dismantled.
BUILDING_KINDS: frozenset[str] = GROUND_LAYER_KINDS | frozenset(
    {WOOD_WALL, STONE_WALL, DOOR, BED, CHAIR, TABLE, WORKSHOP_TABLE}
)

ITEM_KINDS: frozenset[str] = (
    frozenset(
        {
            BERRY,
            WOOD,
            STONE,
            AXE,
            PICKAXE,
            SWORD,
            CHEST,
            MESSAGE_BOARD,
            FIBER,
            CLAY,
            PLANK,
            ROPE,
        }
    )
    | BUILDING_KINDS
)

# Items that can be placed in the world as an object.
PLACEABLE_KINDS: frozenset[str] = frozenset({CHEST, MESSAGE_BOARD}) | BUILDING_KINDS

# --- Blocking, dismantling, resting ------------------------------------------

# Object types nobody can walk through.
BLOCKING_OBJECT_TYPES: frozenset[str] = frozenset({WOOD_WALL, STONE_WALL})
# Object types wolves cannot walk through (settlers open doors, wolves cannot).
WOLF_BLOCKING_OBJECT_TYPES: frozenset[str] = BLOCKING_OBJECT_TYPES | frozenset({DOOR})

# Work units (one per extract action, no tool bonus) to dismantle a building.
DISMANTLE_WORK = 3

# Health restored by one successful rest action on a bed.
REST_HEAL = 2

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

REEDS = "reeds"
CLAY_DEPOSIT = "clay_deposit"

ROCK_TYPES: frozenset[str] = frozenset(
    {"rock_small", "rock_medium", "rock_large", "boulder"}
)
EXTRACTABLE_TYPES: frozenset[str] = frozenset({TREE, REEDS, CLAY_DEPOSIT}) | ROCK_TYPES

# Objects the terrain generator places; ground-layer items may not cover them.
NATURAL_OBJECT_TYPES: frozenset[str] = EXTRACTABLE_TYPES | frozenset({BUSH})

# object_type -> units of material the untouched object holds
DEFAULT_REMAINING: Mapping[str, int] = {
    TREE: 4,
    "rock_small": 1,
    "rock_medium": 2,
    "rock_large": 4,
    "boulder": 6,
    REEDS: 3,
    CLAY_DEPOSIT: 6,
}

# object_type -> item kind yielded per extracted unit
EXTRACT_YIELD: Mapping[str, str] = {
    TREE: WOOD,
    "rock_small": STONE,
    "rock_medium": STONE,
    "rock_large": STONE,
    "boulder": STONE,
    REEDS: FIBER,
    CLAY_DEPOSIT: CLAY,
}

# object_type -> the wielded tool that speeds extraction up
EXTRACT_TOOL: Mapping[str, str] = {
    TREE: AXE,
    "rock_small": PICKAXE,
    "rock_medium": PICKAXE,
    "rock_large": PICKAXE,
    "boulder": PICKAXE,
    CLAY_DEPOSIT: PICKAXE,
}

# Work units needed for one unit of material.
EXTRACT_THRESHOLD = 3
EXTRACT_WORK_BARE = 1
EXTRACT_WORK_WITH_TOOL = 3

# Item kind placed by a PlaceIntent -> object_type created.
PLACED_OBJECT_TYPE: Mapping[str, str] = {
    CHEST: CHEST_OBJECT,
    MESSAGE_BOARD: MESSAGE_BOARD_OBJECT,
    **{kind: kind for kind in sorted(BUILDING_KINDS)},
}


def attack_damage(entity_type: str, wielded: str) -> int:
    """Damage one attack of this entity type wielding `wielded` deals."""
    base = BASE_ATTACK_DAMAGE.get(entity_type, DEFAULT_ATTACK_DAMAGE)
    return base + WIELD_DAMAGE_BONUS.get(wielded, 0)


def default_remaining(object_type: str) -> int:
    """Units of material an untouched object of this type holds."""
    return DEFAULT_REMAINING.get(object_type, 0)
