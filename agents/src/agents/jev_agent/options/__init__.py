"""Enumeration of the actions Jev may choose from on a given tick.

Jev can only pick an option that code put in front of it, so this package is the
agent's real rulebook: every option here is legal *right now* according to the
world model, and each carries the proto Intent it will turn into. Options are
generated in priority order and truncated at `MAX_OPTIONS`, so the tail of the
list is what gets dropped when a tick is unusually rich.

Dismantling is deliberately absent. `ExtractIntent` on a placed building takes
it apart, and Jev reads "extract" as "gather materials", so offering it would
let a settler quietly eat the town wall it just built. Dismantling stays a
planner tool, where a coordinate and a reason are available.

Inside the package the order is `common` (constants and the tiny shared
builders, importing no sibling), then the one-section modules `steps`,
`survival`, `social`, `interaction` and `crafting`, then `base`, which stitches
a tick's list together out of them.
"""

from __future__ import annotations

from .base import enumerate_options, options_to_criteria
from .common import (
    CRAFT_OPTION_LIMIT,
    CRAFT_RECIPES,
    HAIL_KEY_PREFIX,
    HEARD_SHOUT_KEY_PREFIX,
    HEARD_SHOUT_MAX_AGE_TICKS,
    JEV_PLACEABLE_KINDS,
    JOIN_CONVERSATION_KEY_PREFIX,
    JOIN_CONVERSATION_OPTION_LIMIT,
    KEEP_GOING,
    MAX_BRIEF_SHOUTS,
    MAX_OPTIONS,
    MAX_SHOUT_LENGTH,
    OPTION_SECTIONS,
    PLACEABLE_KINDS,
    PLACE_OPTION_LIMIT,
    RECIPES,
    SHOUT_COOLDOWN_TICKS,
    SHOUT_KEY_PREFIX,
    STEP_GROUPS,
    STEP_KEY_PREFIX,
    STEP_SETTLER_LIMIT,
    STEP_TARGETS_PER_GROUP,
    STEP_TARGET_TYPES,
    STOP_GOING,
    WAIT,
    WIELDABLE_KINDS,
    WOLF_ALERT_RADIUS,
    travel_state_for,
)
from .steps import brief_object_ids, step_target_ids
from .survival import retreat_option
from ..briefs import BriefHail, Option, TravelState

__all__ = [
    "BriefHail",
    "CRAFT_OPTION_LIMIT",
    "CRAFT_RECIPES",
    "HAIL_KEY_PREFIX",
    "HEARD_SHOUT_KEY_PREFIX",
    "HEARD_SHOUT_MAX_AGE_TICKS",
    "JEV_PLACEABLE_KINDS",
    "JOIN_CONVERSATION_KEY_PREFIX",
    "JOIN_CONVERSATION_OPTION_LIMIT",
    "KEEP_GOING",
    "MAX_BRIEF_SHOUTS",
    "MAX_OPTIONS",
    "MAX_SHOUT_LENGTH",
    "OPTION_SECTIONS",
    "Option",
    "PLACEABLE_KINDS",
    "PLACE_OPTION_LIMIT",
    "RECIPES",
    "SHOUT_COOLDOWN_TICKS",
    "SHOUT_KEY_PREFIX",
    "STEP_GROUPS",
    "STEP_KEY_PREFIX",
    "STEP_SETTLER_LIMIT",
    "STEP_TARGETS_PER_GROUP",
    "STEP_TARGET_TYPES",
    "STOP_GOING",
    "TravelState",
    "WAIT",
    "WIELDABLE_KINDS",
    "WOLF_ALERT_RADIUS",
    "brief_object_ids",
    "enumerate_options",
    "options_to_criteria",
    "retreat_option",
    "step_target_ids",
    "travel_state_for",
]
