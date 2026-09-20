"""The agent's view of the game's rules, plus the prose it wraps them in.

The rules themselves — kinds, recipes, blocking sets, extraction tables and
every number behind food, fatigue, sleep, combat, wolves, conversations and
signs — live in `bobgame_rules` (repo root `rules/`), which the world enforces
from the same definitions. This module re-exports them under the names the
agent code uses and adds what is genuinely agent-side: where things grow, how
a recipe or a habitat reads in a prompt, and the planner-side thresholds that
are not world physics.
"""

from __future__ import annotations

from typing import Mapping

from bobgame_rules.body import (
    COLLAPSE_WAKE_FATIGUE,
    FATIGUE_INTERVAL_DAY,
    FATIGUE_INTERVAL_NIGHT,
    FOOD_INTERVAL_TICKS,
    HUNGRY_WAKE_FOOD,
    MIN_SLEEP_FATIGUE,
    REGEN_FOOD_THRESHOLD,
    REGEN_INTERVAL_TICKS,
    RESPAWN_DELAY_TICKS,
    RESPAWN_FATIGUE,
    RESPAWN_FOOD,
    SLEEP_RECOVERY,
    STARVATION_DAMAGE,
    STARVATION_INTERVAL_TICKS,
    TIRED_FATIGUE,
)
from bobgame_rules.clock import (
    DEFAULT_DAY_LENGTH_TICKS,
    NEW_MOON_STILL_TICKS,
    night_start_tick,
)
from bobgame_rules.entities import (
    DEFAULT_ATTACK_DAMAGE,
    PLAYER_MAX_FATIGUE,
    PLAYER_MAX_HEALTH,
    WIELD_DAMAGE_BONUS,
    WIELDABLE_KINDS,
    WOLF_ATTACK_DAMAGE,
    WOLF_MAX_HEALTH,
)
from bobgame_rules.items import (
    ANVIL,
    AXE,
    AXE_TOOLS,
    BED,
    BERRY,
    BERRY_FOOD_RESTORE,
    BLOCKING_OBJECT_TYPES,
    BUILDING_KINDS,
    BUSH,
    CHAIR,
    CHARCOAL,
    CHEST,
    CLAY,
    CLAY_DEPOSIT,
    COPPER_AXE,
    COPPER_INGOT,
    COPPER_ORE,
    COPPER_PICKAXE,
    COPPER_VEIN,
    DEFAULT_REMAINING,
    DISMANTLE_WORK,
    DOOR,
    EXTRACT_THRESHOLD,
    EXTRACT_TOOLS,
    EXTRACT_WORK_BARE,
    EXTRACT_WORK_BY_TOOL,
    EXTRACT_YIELD,
    EXTRACTABLE_TYPES,
    FIBER,
    FURNACE,
    GROUND_LAYER_KINDS,
    IRON_AXE,
    IRON_INGOT,
    IRON_ORE,
    IRON_PICKAXE,
    IRON_SWORD,
    IRON_VEIN,
    ITEM_PILE,
    MESSAGE_BOARD,
    METAL_PICKAXE_TOOLS,
    NATURAL_OBJECT_TYPES,
    OWNER_KEY,
    PICKAXE,
    PICKAXE_TOOLS,
    PLACEABLE_KINDS,
    PLANK,
    REEDS,
    REST_HEAL,
    ROAD,
    ROCK_TYPES,
    ROPE,
    SIGN,
    STATION_KINDS,
    STONE,
    STONE_FLOOR,
    STONE_WALL,
    SWORD,
    TABLE,
    TREE,
    VEIN_REQUIRED_TOOLS,
    VEIN_TYPES,
    WOOD,
    WOOD_FLOOR,
    WOOD_WALL,
    WORKSHOP_TABLE,
    can_extract,
    extract_work,
    source_object_types,
)
from bobgame_rules.recipes import CRAFT_PROGRESS_PREFIX, RECIPES, Recipe
from bobgame_rules.social import (
    CONVERSATION,
    CONVERSATION_CHANNEL,
    CONVERSATION_MAX_PARTICIPANTS,
    CONVERSATION_MAX_UTTERANCES,
    CONVERSATION_TEXT_LIMIT,
    CONVERSATION_TRANSCRIPT_KEPT,
    CONVERSATION_TURN_TICKS,
    CONVERSATION_LONELY_TICKS,
    CONVERSE_ACTION_TYPE,
    CONVERSE_HAIL,
    HAIL_COOLDOWN_TICKS,
    HAILED_DETAIL,
    LOCAL_CHANNEL,
    SAY_RADIUS,
    SHOUT_CHANNEL,
    SHOUT_RADIUS,
    SIGN_AUTHOR_KEY,
    SIGN_SLOT,
    SIGN_TEXT_KEY,
    SIGN_TEXT_MAX,
    SIGN_TICK_KEY,
    VIEW_RADIUS,
)
from bobgame_rules.terrain import FloorType

__all__ = [
    "ACTION_HAIL",
    "ACTION_HAILED",
    "ANVIL",
    "AXE",
    "AXE_TOOLS",
    "BED",
    "BERRY",
    "BERRY_FOOD_RESTORE",
    "BLOCKING_OBJECT_TYPES",
    "BUILDING_KINDS",
    "BUSH",
    "BUSH_WATER_MAX_DISTANCE",
    "BUSH_WATER_MIN_DISTANCE",
    "CHAIR",
    "CHARCOAL",
    "CHEST",
    "CLAY",
    "CLAY_DEPOSIT",
    "CLAY_MAX_DISTANCE",
    "CLAY_MIN_DISTANCE",
    "COLLAPSE_WAKE_FATIGUE",
    "CONVERSATION",
    "CONVERSATION_CHANNEL",
    "CONVERSATION_LONELY_TICKS",
    "CONVERSATION_MAX_PARTICIPANTS",
    "CONVERSATION_MAX_UTTERANCES",
    "CONVERSATION_TEXT_LIMIT",
    "CONVERSATION_TRANSCRIPT_KEPT",
    "CONVERSATION_TURN_TICKS",
    "CONVERSE_ACTION_TYPE",
    "CONVERSE_HAIL",
    "COPPER_AXE",
    "COPPER_INGOT",
    "COPPER_ORE",
    "COPPER_PICKAXE",
    "COPPER_VEIN",
    "CRAFT_PROGRESS_PREFIX",
    "CRAFT_RECIPES",
    "DEFAULT_ATTACK_DAMAGE",
    "DEFAULT_DAY_LENGTH_TICKS",
    "DEFAULT_REMAINING",
    "DEFAULT_SETTLER_COUNT",
    "DISMANTLE_WORK",
    "DOOR",
    "EXHAUSTED",
    "EXTRACTABLE_TYPES",
    "EXTRACT_THRESHOLD",
    "EXTRACT_TOOLS",
    "EXTRACT_WORK_BARE",
    "EXTRACT_WORK_BY_TOOL",
    "EXTRACT_YIELD",
    "FATIGUE_INTERVAL_DAY",
    "FATIGUE_INTERVAL_NIGHT",
    "FIBER",
    "FOOD_ALERT_AT",
    "FOOD_INTERVAL_TICKS",
    "FRESH",
    "FURNACE",
    "FloorType",
    "GROUND_LAYER_KINDS",
    "HABITAT_TEXT",
    "HAILED_DETAIL",
    "HAIL_COOLDOWN_TICKS",
    "HUNGRY_WAKE_FOOD",
    "IRON_AXE",
    "IRON_INGOT",
    "IRON_ORE",
    "IRON_PICKAXE",
    "IRON_SWORD",
    "IRON_VEIN",
    "ITEM_PILE",
    "LOCAL_CHANNEL",
    "MESSAGE_BOARD",
    "METAL_PICKAXE_TOOLS",
    "MIN_SLEEP_FATIGUE",
    "NATURAL_OBJECT_TYPES",
    "NEW_MOON_STILL_TICKS",
    "ORE_EXCLUSION_RADIUS",
    "OWNER_KEY",
    "PICKAXE",
    "PICKAXE_TOOLS",
    "PLACEABLE_KINDS",
    "PLANK",
    "PLAYER_MAX_FATIGUE",
    "PLAYER_MAX_HEALTH",
    "RECIPES",
    "REEDS",
    "REED_BANK_WIDTH",
    "REED_COAST_EXCLUSION",
    "REGEN_FOOD_THRESHOLD",
    "REGEN_INTERVAL_TICKS",
    "RESPAWN_DELAY_TICKS",
    "RESPAWN_FATIGUE",
    "RESPAWN_FOOD",
    "RESPAWN_RING_TEXT",
    "REST_HEAL",
    "ROAD",
    "ROCK_TYPES",
    "ROPE",
    "Recipe",
    "SAY_RADIUS",
    "SHOUT_CHANNEL",
    "SHOUT_RADIUS",
    "SIGN",
    "SIGN_AUTHOR_KEY",
    "SIGN_SLOT",
    "SIGN_TEXT_KEY",
    "SIGN_TEXT_MAX",
    "SIGN_TICK_KEY",
    "SLEEP_RECOVERY",
    "STARVATION_DAMAGE",
    "STARVATION_INTERVAL_TICKS",
    "STATION_KINDS",
    "STONE",
    "STONE_FLOOR",
    "STONE_WALL",
    "SWORD",
    "TABLE",
    "TIRED",
    "TIRED_FATIGUE",
    "TREE",
    "TREE_COAST_DISTANCE",
    "VEIN_REQUIRED_TOOLS",
    "VEIN_TYPES",
    "VIEW_RADIUS",
    "WATER_BOUND_TYPES",
    "WATER_FLOOR_TYPES",
    "WIELDABLE_KINDS",
    "WIELD_DAMAGE_BONUS",
    "WOLF_ATTACK_DAMAGE",
    "WOLF_MAX_HEALTH",
    "WOOD",
    "WOOD_FLOOR",
    "WOOD_WALL",
    "WORKSHOP_TABLE",
    "can_extract",
    "extract_work",
    "extraction_text",
    "fatigue_word",
    "habitat_lines",
    "habitat_table_text",
    "habitat_text",
    "is_ground_kind",
    "is_water_bound",
    "island_opening",
    "night_start_tick",
    "recipe_table_text",
    "settler_count_word",
    "sleep_recovery_text",
    "source_object_types",
    "source_text",
    "wield_damage_text",
]

# The `ConverseIntent` action that walks up to a settler and addresses it
# (docs/09 section 9), under the names the agent's conversation code uses. The
# world reports it to the hailer as `hail conv_N <target>` and to the target as
# `hailed conv_N <hailer>`.
ACTION_HAIL = CONVERSE_HAIL
ACTION_HAILED = HAILED_DETAIL

# `Tile.floor_type` values that are water. The observation does not say
# whether a water tile is fresh or salt, so anything built on this set must
# say "water", not "fresh water".
WATER_FLOOR_TYPES: frozenset[str] = frozenset(
    {FloorType.SHALLOW_WATER.value, FloorType.DEEP_WATER.value}
)

# Legacy view used by callers that only care about a recipe's inputs.
CRAFT_RECIPES: Mapping[str, Mapping[str, int]] = {
    name: dict(recipe.inputs) for name, recipe in RECIPES.items()
}

# --- Agent-side thresholds -------------------------------------------------
#
# Not world physics: nothing in the world changes at these numbers. They are
# the levels at which this agent tells itself something.

# Food at or below this is stated as a `!!` line on every planner tool result,
# and it is the level a Jev stint hands the body back at (`stint.py`).
FOOD_ALERT_AT = 25

# How many settlers a scenario starts with. `settlement.toml` has twelve;
# `hamlet.toml` has six and passes `--settlers 6` to every agent process.
DEFAULT_SETTLER_COUNT = 12

# Where a respawn puts you, as the prompt says it (`RESPAWN_RING_DISTANCES`).
RESPAWN_RING_TEXT = "12 or 24 tiles"

# The words this agent uses for its own fatigue.
FRESH = "fresh"
TIRED = "tired"
EXHAUSTED = "exhausted"


def extraction_text(object_type: str) -> str:
    """How an object is worked: bare hands, faster with a tool, or tool-only."""
    required = VEIN_REQUIRED_TOOLS.get(object_type, frozenset())
    if required:
        return f"needs {_tool_phrase(required)} in hand"
    speeds = EXTRACT_TOOLS.get(object_type, frozenset())
    if speeds:
        return f"bare hands, faster with {_tool_phrase(speeds)}"
    return "bare hands"


def _tool_phrase(tools: frozenset[str]) -> str:
    """`"a pickaxe"` for a family of tools, naming the plainest one."""
    for name in (AXE, PICKAXE, COPPER_PICKAXE, IRON_PICKAXE):
        if name in tools:
            return f"a {name}"
    return "a " + sorted(tools)[0]


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
    sentence = f"{kind} comes from {parts}"
    habitats = habitat_lines(kind)
    if habitats:
        sentence = "; ".join([sentence, *habitats])
    return sentence


# --- Where things grow (mirrors world/terrain/objects.py and its config) -----
#
# The numbers are `ObjectPlacementConfig` in `world/src/world/terrain/config.py`
# and `ORE_EXCLUSION_RADIUS` in `world/src/world/settlement.py`. Both are
# generation-time configuration rather than rules of play, so they are not in
# `bobgame_rules`. "Fresh water" is what the generator's `dist_to_fresh` field
# measures: lakes, rivers and the fords across them, never the sea.

# `reed_bank_width`: how far from fresh water a bank reed grows.
REED_BANK_WIDTH = 2
# `reed_coast_exclusion`: no reeds this close to the ocean.
REED_COAST_EXCLUSION = 12
# `clay_min_distance` / `clay_max_distance`: the band back from fresh water.
CLAY_MIN_DISTANCE = 2
CLAY_MAX_DISTANCE = 7
# `bush_water_min_distance` / `bush_water_max_distance`.
BUSH_WATER_MIN_DISTANCE = 4
BUSH_WATER_MAX_DISTANCE = 60
# `tree_coast_distance`: trees reach full density this far in from the ocean.
TREE_COAST_DISTANCE = 40
# `settlement.ORE_EXCLUSION_RADIUS`: no vein survives this close to the site.
ORE_EXCLUSION_RADIUS = 60

_VEIN_HABITAT = (
    "veins sit in rock outcrops on high ground - ridges, hillsides and "
    f"mountain feet - and never within {ORE_EXCLUSION_RADIUS} tiles of the "
    "settlement site"
)

# object_type -> where on the island that object grows or sits. Physics, not
# advice: every clause restates a rule the terrain generator actually applies.
HABITAT_TEXT: Mapping[str, str] = {
    REEDS: (
        "reeds grow on the banks and in the shallows of fresh water (lakes, "
        f"rivers and their fords), within {REED_BANK_WIDTH} tiles of the "
        f"water, and never within {REED_COAST_EXCLUSION} tiles of the sea"
    ),
    CLAY_DEPOSIT: (
        f"clay deposits lie in tight patches {CLAY_MIN_DISTANCE} to "
        f"{CLAY_MAX_DISTANCE} tiles back from fresh water, on grass or dirt"
    ),
    TREE: (
        "trees grow in stands and copses inland, thinning out within "
        f"{TREE_COAST_DISTANCE} tiles of the sea and on stony outcrops; they "
        "come right down to lake and river banks"
    ),
    BUSH: (
        "berry bushes grow in thickets along the edges of woodland, at least "
        f"{BUSH_WATER_MIN_DISTANCE} tiles from any water and thinning out "
        f"beyond {BUSH_WATER_MAX_DISTANCE} tiles from it"
    ),
    COPPER_VEIN: f"copper {_VEIN_HABITAT}",
    IRON_VEIN: f"iron {_VEIN_HABITAT}",
}
HABITAT_TEXT = {
    **HABITAT_TEXT,
    **{
        rock: (
            "rocks and boulders gather in outcrops along ridges and at "
            "mountain feet, with a thin scatter of strays on open ground"
        )
        for rock in ROCK_TYPES
    },
}

# Object types whose habitat is tied to fresh water, so knowing where the
# nearest water is narrows the search.
WATER_BOUND_TYPES: frozenset[str] = frozenset({REEDS, CLAY_DEPOSIT})


def habitat_text(object_type: str) -> str:
    """Where `object_type` is found on the island, or `""` if no rule is stated."""
    return HABITAT_TEXT.get(object_type, "")


def habitat_lines(kind: str) -> list[str]:
    """The distinct habitat sentences for every object type yielding `kind`."""
    seen: list[str] = []
    for object_type in source_object_types(kind):
        text = habitat_text(object_type)
        if text and text not in seen:
            seen.append(text)
    return seen


def is_water_bound(kind: str) -> bool:
    """Whether every source of `kind` grows within a few tiles of fresh water."""
    sources = source_object_types(kind)
    return bool(sources) and all(
        object_type in WATER_BOUND_TYPES for object_type in sources
    )


def habitat_table_text() -> str:
    """ "Where things are found" as prompt lines, one per habitat, deduplicated."""
    lines: list[str] = []
    seen: set[str] = set()
    for object_type in (TREE, BUSH, REEDS, CLAY_DEPOSIT, *sorted(ROCK_TYPES)):
        text = habitat_text(object_type)
        if not text or text in seen:
            continue
        seen.add(text)
        lines.append(f"  {text}.")
    lines.append(f"  Copper and iron {_VEIN_HABITAT}.")
    return "\n".join(lines)


# --- Prompt lines over the rules -------------------------------------------


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


def island_opening(settler_count: int = DEFAULT_SETTLER_COUNT) -> str:
    """The setting and the goal, shared by every prompt that needs them.

    The planner's narrative and the journal writer's narrative both open with
    this, so the goal a settler plans towards and the goal it writes its
    `Tomorrow` section against cannot drift apart.
    """
    return f"""\
You are one of {settler_count_word(settler_count)} people who woke up together on a large, wild island with
nothing but your hands. The others are real agents like you; they hear what you
say and read what you write. Together, build a civilization: a settlement that
lasts, where every one of you has a shelter of your own to sleep in, and where
food, safety and rest are things you can count on tomorrow and not only today.
Each of you also has to find your place in it: what you do, whom you work with,
and what you are known for."""


def wield_damage_text() -> str:
    """`"sword +3, iron_sword +5, ..."`: every wielded weapon's damage bonus."""
    return ", ".join(f"{kind} +{bonus}" for kind, bonus in WIELD_DAMAGE_BONUS.items())


def fatigue_word(fatigue: int) -> str:
    """`fresh`, `tired` or `exhausted` for a fatigue value."""
    if fatigue >= PLAYER_MAX_FATIGUE:
        return EXHAUSTED
    if fatigue >= TIRED_FATIGUE:
        return TIRED
    return FRESH


def sleep_recovery_text(on_bed: bool, night: bool) -> str:
    """`"1 fatigue per tick"` or `"2 fatigue per 3 ticks"` for these conditions."""
    points, ticks = SLEEP_RECOVERY[(on_bed, night)]
    if ticks == 1:
        return f"{points} fatigue per tick"
    return f"{points} fatigue per {ticks} ticks"


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
