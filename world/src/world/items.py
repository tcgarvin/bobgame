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
BUILDING_KINDS: frozenset[str] = GROUND_LAYER_KINDS | frozenset(
    {
        WOOD_WALL,
        STONE_WALL,
        DOOR,
        SIGN,
        BED,
        CHAIR,
        TABLE,
        WORKSHOP_TABLE,
        FURNACE,
        ANVIL,
    }
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

# --- Conversations (docs/09_conversation_and_reflex.md) -------------------

# Object type of a conversation, sitting on its anchor tile.
CONVERSATION = "conversation"
# Utterance channel used by `speak`; heard at the `local` radius.
CONVERSATION_CHANNEL = "conversation"
# Seats, including the opener.
CONVERSATION_MAX_PARTICIPANTS = 4
# A turn not used within this many ticks counts as a pass.
CONVERSATION_TURN_TICKS = 10
# A conversation that never gained a second participant closes after this.
CONVERSATION_LONELY_TICKS = 20
# A conversation closes after this many `speak` actions.
CONVERSATION_MAX_UTTERANCES = 40
# Characters per line; longer text is truncated.
CONVERSATION_TEXT_LIMIT = 300
# Transcript lines kept in object state.
CONVERSATION_TRANSCRIPT_KEPT = 12
# How long a spoken invitation ("open to talk", docs/09, section 8) stays open.
INVITATION_TICKS = 40
# How long after its last conversation ended a settler cannot be hailed
# (docs/09, section 9). Invitations, joining and opening are unaffected.
HAIL_COOLDOWN_TICKS = 60

# --- Signs (docs/08_building.md, "Signs") ---------------------------------

# A sign holds one line. Longer text is refused, never truncated.
SIGN_TEXT_MAX = 80
# Object state keys a placed sign carries.
SIGN_TEXT_KEY = "text"
SIGN_AUTHOR_KEY = "author"
SIGN_TICK_KEY = "tick"
# A sign has one slot, so `WriteNoteIntent.slot` must be this.
SIGN_SLOT = 0

# --- Combat ---------------------------------------------------------------

BASE_ATTACK_DAMAGE: Mapping[str, int] = {"wolf": 3}
DEFAULT_ATTACK_DAMAGE = 2

WIELD_DAMAGE_BONUS: Mapping[str, int] = {
    SWORD: 3,
    IRON_SWORD: 5,
    AXE: 2,
    COPPER_AXE: 2,
    IRON_AXE: 3,
    PICKAXE: 1,
    COPPER_PICKAXE: 1,
    IRON_PICKAXE: 2,
}

# Kinds a settler can wield; everything else in hand gives no bonus.
WIELDABLE_KINDS: frozenset[str] = frozenset(WIELD_DAMAGE_BONUS)

# --- Food -----------------------------------------------------------------

# kind -> food restored per unit eaten
FOOD_RESTORE: Mapping[str, int] = {BERRY: 20}

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

# Ore veins need a pickaxe of a high enough tier to work at all.
VEIN_REQUIRED_TOOLS: Mapping[str, frozenset[str]] = {
    COPPER_VEIN: frozenset({PICKAXE, COPPER_PICKAXE, IRON_PICKAXE}),
    IRON_VEIN: frozenset({COPPER_PICKAXE, IRON_PICKAXE}),
}

ROCK_TYPES: frozenset[str] = frozenset(
    {"rock_small", "rock_medium", "rock_large", "boulder"}
)
EXTRACTABLE_TYPES: frozenset[str] = (
    frozenset({TREE, REEDS, CLAY_DEPOSIT, COPPER_VEIN, IRON_VEIN}) | ROCK_TYPES
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
_AXES: frozenset[str] = frozenset({AXE, COPPER_AXE, IRON_AXE})
_PICKAXES: frozenset[str] = frozenset({PICKAXE, COPPER_PICKAXE, IRON_PICKAXE})

EXTRACT_TOOLS: Mapping[str, frozenset[str]] = {
    TREE: _AXES,
    "rock_small": _PICKAXES,
    "rock_medium": _PICKAXES,
    "rock_large": _PICKAXES,
    "boulder": _PICKAXES,
    CLAY_DEPOSIT: _PICKAXES,
    COPPER_VEIN: _PICKAXES,
    IRON_VEIN: frozenset({COPPER_PICKAXE, IRON_PICKAXE}),
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
EXTRACT_WORK_BARE = 1

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
