"""Item kinds, object kinds, and the tables that relate them.

The single source of truth for what can be held, wielded, extracted, eaten and
placed. See docs/05_jev_agents_design.md ("Game mechanics") and
docs/08_building.md.
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

# Ores, fuel and metal (docs/10_metal_and_sleep.md).
COPPER_ORE = "copper_ore"
IRON_ORE = "iron_ore"
CHARCOAL = "charcoal"
COPPER_INGOT = "copper_ingot"
IRON_INGOT = "iron_ingot"

# Metal tools. Copper improves gathering, iron improves gathering and fighting.
COPPER_AXE = "copper_axe"
COPPER_PICKAXE = "copper_pickaxe"
IRON_AXE = "iron_axe"
IRON_PICKAXE = "iron_pickaxe"
IRON_SWORD = "iron_sword"

# Building items. The placed object's object_type equals the item kind.
ROAD = "road"
WOOD_FLOOR = "wood_floor"
STONE_FLOOR = "stone_floor"
WOOD_WALL = "wood_wall"
STONE_WALL = "stone_wall"
DOOR = "door"
SIGN = "sign"
BED = "bed"
CHAIR = "chair"
TABLE = "table"
WORKSHOP_TABLE = "workshop_table"
FURNACE = "furnace"
ANVIL = "anvil"

# Crafting stations: recipes with `station` set need one of these adjacent.
STATION_KINDS: frozenset[str] = frozenset({WORKSHOP_TABLE, FURNACE, ANVIL})

# Ground-layer kinds lie under structures and never block.
GROUND_LAYER_KINDS: frozenset[str] = frozenset({ROAD, WOOD_FLOOR, STONE_FLOOR})

# Everything the building update added; all of it can be dismantled.
BUILDING_KINDS: frozenset[str] = (
    GROUND_LAYER_KINDS
    | frozenset({WOOD_WALL, STONE_WALL, DOOR, SIGN, BED, CHAIR, TABLE})
    | STATION_KINDS
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
            COPPER_ORE,
            IRON_ORE,
            CHARCOAL,
            COPPER_INGOT,
            IRON_INGOT,
            COPPER_AXE,
            COPPER_PICKAXE,
            IRON_AXE,
            IRON_PICKAXE,
            IRON_SWORD,
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

# --- Food -----------------------------------------------------------------

# Food one berry restores.
BERRY_FOOD_RESTORE = 20
# kind -> food restored per unit eaten
FOOD_RESTORE: Mapping[str, int] = {BERRY: BERRY_FOOD_RESTORE}

# --- Object kinds ---------------------------------------------------------

TREE = "tree"
BUSH = "bush"
ITEM_PILE = "item_pile"
CHEST_OBJECT = "chest"
MESSAGE_BOARD_OBJECT = "message_board"

SIGN_OBJECT = "sign"

REEDS = "reeds"
CLAY_DEPOSIT = "clay_deposit"
COPPER_VEIN = "copper_vein"
IRON_VEIN = "iron_vein"

# The object state key a placed object records its placer under.
OWNER_KEY = "owner"

# --- Tool families ---------------------------------------------------------

AXE_TOOLS: frozenset[str] = frozenset({AXE, COPPER_AXE, IRON_AXE})
PICKAXE_TOOLS: frozenset[str] = frozenset({PICKAXE, COPPER_PICKAXE, IRON_PICKAXE})
# Iron ore is hard: a plain stone pickaxe does not bite into it.
METAL_PICKAXE_TOOLS: frozenset[str] = frozenset({COPPER_PICKAXE, IRON_PICKAXE})

# --- Extraction ------------------------------------------------------------

# Ore veins need a pickaxe of a high enough tier to work at all.
VEIN_REQUIRED_TOOLS: Mapping[str, frozenset[str]] = {
    COPPER_VEIN: PICKAXE_TOOLS,
    IRON_VEIN: METAL_PICKAXE_TOOLS,
}

VEIN_TYPES: frozenset[str] = frozenset({COPPER_VEIN, IRON_VEIN})

ROCK_TYPES: frozenset[str] = frozenset(
    {"rock_small", "rock_medium", "rock_large", "boulder"}
)
EXTRACTABLE_TYPES: frozenset[str] = (
    frozenset({TREE, REEDS, CLAY_DEPOSIT}) | ROCK_TYPES | VEIN_TYPES
)

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
    COPPER_VEIN: 4,
    IRON_VEIN: 4,
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
    COPPER_VEIN: COPPER_ORE,
    IRON_VEIN: IRON_ORE,
}

# object_type -> the wielded tools that speed extraction up. Wielding any of
# them yields EXTRACT_WORK_BY_TOOL work per action instead of EXTRACT_WORK_BARE.
EXTRACT_TOOLS: Mapping[str, frozenset[str]] = {
    TREE: AXE_TOOLS,
    "rock_small": PICKAXE_TOOLS,
    "rock_medium": PICKAXE_TOOLS,
    "rock_large": PICKAXE_TOOLS,
    "boulder": PICKAXE_TOOLS,
    CLAY_DEPOSIT: PICKAXE_TOOLS,
    COPPER_VEIN: PICKAXE_TOOLS,
    IRON_VEIN: METAL_PICKAXE_TOOLS,
}

# tool kind -> work units one extract action adds while wielding it.
EXTRACT_WORK_BY_TOOL: Mapping[str, int] = {
    AXE: 3,
    PICKAXE: 3,
    COPPER_AXE: 4,
    COPPER_PICKAXE: 4,
    IRON_AXE: 5,
    IRON_PICKAXE: 5,
}

# Work units needed for one unit of material.
EXTRACT_THRESHOLD = 3
# Work units one extract action adds with nothing useful in hand.
EXTRACT_WORK_BARE = 1

# Item kind placed by a PlaceIntent -> object_type created.
PLACED_OBJECT_TYPE: Mapping[str, str] = {
    CHEST: CHEST_OBJECT,
    MESSAGE_BOARD: MESSAGE_BOARD_OBJECT,
    **{kind: kind for kind in sorted(BUILDING_KINDS)},
}

# Every object type the world can hold, for consumers (the viewer) that must
# have a sprite or a label for each one.
OBJECT_TYPES: frozenset[str] = (
    NATURAL_OBJECT_TYPES
    | BUILDING_KINDS
    | frozenset({ITEM_PILE, CHEST_OBJECT, MESSAGE_BOARD_OBJECT})
)


def default_remaining(object_type: str) -> int:
    """Units of material an untouched object of this type holds."""
    return DEFAULT_REMAINING.get(object_type, 0)


def extract_work(wielded: str, object_type: str) -> int:
    """Work units one extract action adds while wielding `wielded`.

    A tool only helps on the object types it is meant for; anything else in
    hand, or an empty hand, adds `EXTRACT_WORK_BARE`.
    """
    if wielded and wielded in EXTRACT_TOOLS.get(object_type, frozenset()):
        return EXTRACT_WORK_BY_TOOL.get(wielded, EXTRACT_WORK_BARE)
    return EXTRACT_WORK_BARE


def can_extract(wielded: str, object_type: str) -> bool:
    """Whether `wielded` is good enough to extract from `object_type` at all."""
    required = VEIN_REQUIRED_TOOLS.get(object_type, frozenset())
    return not required or wielded in required


def source_object_types(kind: str) -> list[str]:
    """The object types one unit of extraction turns into `kind`, sorted."""
    return sorted(
        object_type for object_type, yielded in EXTRACT_YIELD.items() if yielded == kind
    )
