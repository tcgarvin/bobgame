"""The agent-side mirror of the world's item, recipe and object tables.

The agents package cannot import `world`, so this module restates the contract
in `world/src/world/items.py`, `docs/08_building.md` and
`docs/10_metal_and_sleep.md`. Keep the two in step: if a recipe or a kind
changes there, change it here in the same commit.
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

COPPER_ORE = "copper_ore"
IRON_ORE = "iron_ore"
CHARCOAL = "charcoal"
COPPER_INGOT = "copper_ingot"
IRON_INGOT = "iron_ingot"

AXE = "axe"
PICKAXE = "pickaxe"
SWORD = "sword"
COPPER_AXE = "copper_axe"
COPPER_PICKAXE = "copper_pickaxe"
IRON_AXE = "iron_axe"
IRON_PICKAXE = "iron_pickaxe"
IRON_SWORD = "iron_sword"
CHEST = "chest"
MESSAGE_BOARD = "message_board"

# --- Building items (the placed object's type equals the item kind) --------

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

# The three crafting stations. A recipe with a station must be crafted while
# standing on or next to a placed one of that type.
STATION_KINDS: frozenset[str] = frozenset({WORKSHOP_TABLE, FURNACE, ANVIL})

# Ground-layer kinds lie under structures, never block, and are placed on the
# placer's own tile with no direction.
GROUND_LAYER_KINDS: frozenset[str] = frozenset({ROAD, WOOD_FLOOR, STONE_FLOOR})

BUILDING_KINDS: frozenset[str] = (
    GROUND_LAYER_KINDS
    | frozenset({WOOD_WALL, STONE_WALL, DOOR, SIGN, BED, CHAIR, TABLE})
    | STATION_KINDS
)

PLACEABLE_KINDS: frozenset[str] = frozenset({CHEST, MESSAGE_BOARD}) | BUILDING_KINDS

# The object state key a placed object records its placer under
# (`world/containers.py`, `OWNER_KEY`).
OWNER_KEY = "owner"

WIELDABLE_KINDS: frozenset[str] = frozenset(
    {
        AXE,
        PICKAXE,
        SWORD,
        COPPER_AXE,
        COPPER_PICKAXE,
        IRON_AXE,
        IRON_PICKAXE,
        IRON_SWORD,
    }
)

# --- Signs (mirrors world/items.py, docs/08_building.md "Signs") ------------

# One line, refused outright when it is longer; never truncated.
SIGN_TEXT_MAX = 80
# Object state keys a placed sign carries.
SIGN_TEXT_KEY = "text"
SIGN_AUTHOR_KEY = "author"
SIGN_TICK_KEY = "tick"
# A sign has one slot, so `WriteNoteIntent.slot` is always this.
SIGN_SLOT = 0
# How far away a sign is read from: the observation view radius.
SIGN_READ_RADIUS = 8

# --- Natural objects -------------------------------------------------------

TREE = "tree"
BUSH = "bush"
REEDS = "reeds"
CLAY_DEPOSIT = "clay_deposit"
COPPER_VEIN = "copper_vein"
IRON_VEIN = "iron_vein"
ITEM_PILE = "item_pile"

ROCK_TYPES: frozenset[str] = frozenset(
    {"rock_small", "rock_medium", "rock_large", "boulder"}
)
VEIN_TYPES: frozenset[str] = frozenset({COPPER_VEIN, IRON_VEIN})
EXTRACTABLE_TYPES: frozenset[str] = (
    frozenset({TREE, REEDS, CLAY_DEPOSIT}) | ROCK_TYPES | VEIN_TYPES
)

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
    COPPER_VEIN: 4,
    IRON_VEIN: 4,
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
    COPPER_VEIN: COPPER_ORE,
    IRON_VEIN: IRON_ORE,
}

AXE_TOOLS: frozenset[str] = frozenset({AXE, COPPER_AXE, IRON_AXE})
PICKAXE_TOOLS: frozenset[str] = frozenset({PICKAXE, COPPER_PICKAXE, IRON_PICKAXE})
# Iron ore is hard: a plain stone pickaxe does not bite into it.
METAL_PICKAXE_TOOLS: frozenset[str] = frozenset({COPPER_PICKAXE, IRON_PICKAXE})

# object_type -> the wielded tools that speed extraction up. Anything else in
# the hand (or an empty hand) adds one unit of work per action.
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

# object_type -> the tools without which extraction fails outright. Trees,
# rocks, reeds and clay can be worked bare-handed; veins cannot.
EXTRACT_REQUIRED_TOOLS: Mapping[str, frozenset[str]] = {
    COPPER_VEIN: PICKAXE_TOOLS,
    IRON_VEIN: METAL_PICKAXE_TOOLS,
}

# Work units one extract action adds, by wielded tool ("" is bare hands).
EXTRACT_WORK_BY_TOOL: Mapping[str, int] = {
    "": 1,
    AXE: 3,
    PICKAXE: 3,
    COPPER_AXE: 4,
    COPPER_PICKAXE: 4,
    IRON_AXE: 5,
    IRON_PICKAXE: 5,
}

# Work units one unit of material costs.
EXTRACT_THRESHOLD = 3

# Extract actions needed to dismantle one placed building object.
DISMANTLE_WORK = 3

# Health one successful rest on a bed restores.
REST_HEAL = 2


def extract_work_per_action(object_type: str, wielded: str) -> int:
    """Work units one extract action on `object_type` adds while wielding `wielded`."""
    if wielded not in EXTRACT_TOOLS.get(object_type, frozenset()):
        return EXTRACT_WORK_BY_TOOL[""]
    return EXTRACT_WORK_BY_TOOL.get(wielded, EXTRACT_WORK_BY_TOOL[""])


def can_extract(object_type: str, wielded: str) -> bool:
    """Whether `wielded` is good enough to extract from `object_type` at all."""
    required = EXTRACT_REQUIRED_TOOLS.get(object_type, frozenset())
    return not required or wielded in required


def source_object_types(kind: str) -> list[str]:
    """The object types one unit of extraction turns into `kind`, sorted."""
    return sorted(
        object_type for object_type, yielded in EXTRACT_YIELD.items() if yielded == kind
    )


def _tool_phrase(tools: frozenset[str]) -> str:
    """`"a pickaxe"` for a family of tools, naming the plainest one."""
    for name in (AXE, PICKAXE, COPPER_PICKAXE, IRON_PICKAXE):
        if name in tools:
            return f"a {name}"
    return "a " + sorted(tools)[0]


def extraction_text(object_type: str) -> str:
    """How an object is worked: bare hands, faster with a tool, or tool-only."""
    required = EXTRACT_REQUIRED_TOOLS.get(object_type, frozenset())
    if required:
        return f"needs {_tool_phrase(required)} in hand"
    speeds = EXTRACT_TOOLS.get(object_type, frozenset())
    if speeds:
        return f"bare hands, faster with {_tool_phrase(speeds)}"
    return "bare hands"


def source_text(kind: str) -> str:
    """`"fiber comes from reeds (bare hands)"`, or `""` when nothing yields it.

    Generic over the extraction map, so a new material needs no new code here.
    """
    sources = source_object_types(kind)
    if not sources:
        return ""
    # Four kinds of rock all yield stone the same way; say it once.
    by_method: dict[str, list[str]] = {}
    for object_type in sources:
        by_method.setdefault(extraction_text(object_type), []).append(object_type)
    parts = ", ".join(
        f"{' or '.join(types)} ({method})" for method, types in by_method.items()
    )
    return f"{kind} comes from {parts}"


# --- Combat and speech (mirrors world/items.py, world/wolves.py) -------------

PLAYER_MAX_HEALTH = 20
UNARMED_DAMAGE = 2
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
WOLF_HEALTH = 16
WOLF_DAMAGE = 3

# --- Food and health (mirrors world/stats.py) -----------------------------

# Ticks per point of food lost while awake.
FOOD_INTERVAL_TICKS = 4
# At food 0, this much damage every this many ticks.
STARVATION_INTERVAL_TICKS = 4
STARVATION_DAMAGE = 1
# Health only regrows above this food (and only while not tired).
REGEN_FOOD_THRESHOLD = 50
BERRY_FOOD_RESTORE = 20
# Food at or below this is stated as a `!!` line on every planner tool result,
# and it is the level a Jev stint hands the body back at (`stint.py`).
FOOD_ALERT_AT = 25

LOCAL_CHANNEL = "local"
SHOUT_CHANNEL = "shout"
SAY_RADIUS = 10
SHOUT_RADIUS = 60

# --- The day and sleep (mirrors world/stats.py, docs/10 sections 3 and 4) ----

DEFAULT_DAY_LENGTH = 300
# The first two thirds of a day are light; the rest is night.
NIGHT_START_FRACTION = 2 / 3

MAX_FATIGUE = 100
# At or above this, tool extraction work per action is halved (minimum 1)
# (minimum 1), attack damage is 1 less (minimum 1), and health stops regrowing.
TIRED_FATIGUE = 60
# A collapsed sleeper wakes at this fatigue and not before.
COLLAPSE_WAKE_FATIGUE = 70
RESPAWN_FATIGUE = 30
# Dying: how long the body is gone, what it comes back with, and where
# (`world/stats.py`: RESPAWN_DELAY_TICKS, RESPAWN_FOOD, RESPAWN_RING_DISTANCES).
RESPAWN_DELAY_TICKS = 10
RESPAWN_FOOD = 50
RESPAWN_RING_TEXT = "12 or 24 tiles"
# Ticks per fatigue point gained while awake.
FATIGUE_INTERVAL_DAY = 4
FATIGUE_INTERVAL_NIGHT = 3
# Ticks a bed sleeper needs per point of health healed.
REGEN_INTERVAL_TICKS = 5
# Food at or below which a sleeper wakes, and below which it cannot fall asleep
# at all (`world/sleep.py`, `HUNGRY_WAKE_FOOD`). One coherent rule: you do not
# lie down that hungry, and if you get that hungry asleep, you wake.
HUNGRY_WAKE_FOOD = 20

FRESH = "fresh"
TIRED = "tired"
EXHAUSTED = "exhausted"

# (on a bed, at night) -> ticks of sleep per fatigue point recovered.
SLEEP_RECOVERY_TICKS: Mapping[tuple[bool, bool], int] = {
    (True, True): 1,
    (True, False): 2,
    (False, True): 2,
    (False, False): 4,
}


# How many settlers a scenario starts with. `settlement.toml` has twelve;
# `hamlet.toml` has six and passes `--settlers 6` to every agent process.
DEFAULT_SETTLER_COUNT = 12

_NUMBER_WORDS: Mapping[int, str] = {
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
    11: "eleven",
    12: "twelve",
}


def settler_count_word(count: int) -> str:
    """`"six"` for 6; counts outside 2..12 are spelled with digits."""
    return _NUMBER_WORDS.get(count, str(count))


def wield_damage_text() -> str:
    """`"sword +3, iron_sword +5, ..."`: every wielded weapon's damage bonus."""
    return ", ".join(f"{kind} +{bonus}" for kind, bonus in WIELD_DAMAGE_BONUS.items())


def fatigue_word(fatigue: int) -> str:
    """`fresh`, `tired` or `exhausted` for a fatigue value."""
    if fatigue >= MAX_FATIGUE:
        return EXHAUSTED
    if fatigue >= TIRED_FATIGUE:
        return TIRED
    return FRESH


def sleep_recovery_text(on_bed: bool, night: bool) -> str:
    """`"1 fatigue per tick"` or `"1 fatigue per 2 ticks"` for these conditions."""
    ticks = SLEEP_RECOVERY_TICKS[(on_bed, night)]
    return "1 fatigue per tick" if ticks == 1 else f"1 fatigue per {ticks} ticks"


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

# A say with `open_to_talk` keeps the speaker open for this many ticks: anyone
# standing next to them may accept and a conversation starts (docs/09 section 8).
INVITATION_TICKS = 40
# The `ConverseIntent` action that takes up an open invitation.
ACTION_ACCEPT = "accept"
# The `ConverseIntent` action that walks up to a settler and addresses it,
# without any invitation (docs/09 section 9). The world reports it to the
# hailer as `hail conv_N <target>` and to the target as
# `hailed conv_N <hailer>`.
ACTION_HAIL = "hail"
ACTION_HAILED = "hailed"
# A settler cannot be hailed until this many ticks after its last conversation
# ended. Invitations, accepting, opening and joining are unaffected.
HAIL_COOLDOWN_TICKS = 60
# `EntityActed.action_type` for every `ConverseIntent`, whatever its action.
CONVERSE_ACTION_TYPE = "converse"


# --- Recipes ---------------------------------------------------------------


@dataclass(frozen=True)
class Recipe:
    """One craftable item: what it eats, how many it yields, where and how long.

    `station` is `""` for a hand recipe, otherwise the object type that must be
    on or next to the crafter. `work` is the number of craft actions the recipe
    takes; every hand recipe takes one and finishes at once.
    """

    inputs: Mapping[str, int]
    output_count: int = 1
    station: str = ""
    work: int = 1

    def cost_text(self) -> str:
        """`"2 wood + 1 stone"`, in the table's order."""
        return " + ".join(f"{amount} {kind}" for kind, amount in self.inputs.items())


RECIPES: Mapping[str, Recipe] = {
    AXE: Recipe({WOOD: 2, STONE: 1}),
    PICKAXE: Recipe({WOOD: 2, STONE: 2}),
    SWORD: Recipe({WOOD: 1, STONE: 3}),
    CHEST: Recipe({WOOD: 6}),
    MESSAGE_BOARD: Recipe({WOOD: 4, STONE: 1}),
    SIGN: Recipe({WOOD: 2}),
    PLANK: Recipe({WOOD: 1}, output_count=2),
    ROPE: Recipe({FIBER: 2}),
    ROAD: Recipe({STONE: 2}, output_count=4),
    WOOD_WALL: Recipe({PLANK: 2}),
    WOOD_FLOOR: Recipe({PLANK: 1}, output_count=2),
    WORKSHOP_TABLE: Recipe({PLANK: 4, STONE: 2}),
    STONE_WALL: Recipe({STONE: 2, CLAY: 1}, station=WORKSHOP_TABLE),
    STONE_FLOOR: Recipe({STONE: 1, CLAY: 1}, output_count=2, station=WORKSHOP_TABLE),
    DOOR: Recipe({PLANK: 3, ROPE: 1}, station=WORKSHOP_TABLE),
    BED: Recipe({PLANK: 4, FIBER: 3}, station=WORKSHOP_TABLE),
    CHAIR: Recipe({PLANK: 2}, station=WORKSHOP_TABLE),
    TABLE: Recipe({PLANK: 4}, station=WORKSHOP_TABLE),
    FURNACE: Recipe({STONE: 8, CLAY: 4}, station=WORKSHOP_TABLE, work=3),
    CHARCOAL: Recipe({WOOD: 3}, output_count=2, station=FURNACE, work=2),
    COPPER_INGOT: Recipe({COPPER_ORE: 2, CHARCOAL: 1}, station=FURNACE, work=3),
    IRON_INGOT: Recipe({IRON_ORE: 2, CHARCOAL: 2}, station=FURNACE, work=4),
    COPPER_AXE: Recipe({PLANK: 2, COPPER_INGOT: 2}, station=WORKSHOP_TABLE, work=2),
    COPPER_PICKAXE: Recipe({PLANK: 2, COPPER_INGOT: 2}, station=WORKSHOP_TABLE, work=2),
    ANVIL: Recipe({IRON_INGOT: 5, STONE: 2}, station=WORKSHOP_TABLE, work=4),
    IRON_AXE: Recipe({PLANK: 2, IRON_INGOT: 2}, station=ANVIL, work=2),
    IRON_PICKAXE: Recipe({PLANK: 2, IRON_INGOT: 2}, station=ANVIL, work=2),
    IRON_SWORD: Recipe({PLANK: 1, IRON_INGOT: 3}, station=ANVIL, work=3),
}

# Legacy view used by callers that only care about the inputs.
CRAFT_RECIPES: Mapping[str, Mapping[str, int]] = {
    name: recipe.inputs for name, recipe in RECIPES.items()
}

# The progress of a multi-action craft is kept in the station object's state
# under this key plus the crafter's entity id, as `"<recipe>:<actions done>"`.
CRAFT_PROGRESS_PREFIX = "craft:"


def recipe_table_text() -> str:
    """The whole recipe table as prompt lines: inputs, yield, station, work."""
    lines: list[str] = []
    for name, recipe in RECIPES.items():
        yields = "" if recipe.output_count == 1 else f" (yields {recipe.output_count})"
        station = recipe.station or "hand"
        lines.append(
            f"  {name} = {recipe.cost_text()}{yields} "
            f"[{station}, {recipe.work} action{'' if recipe.work == 1 else 's'}]"
        )
    return "\n".join(lines)


def is_ground_kind(kind: str) -> bool:
    """Whether this item is placed on the placer's own tile with no direction."""
    return kind in GROUND_LAYER_KINDS
