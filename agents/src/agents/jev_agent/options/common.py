"""Constants and the tiny builders every option section shares.

The bottom of the `options` package: it imports nothing from its siblings, so
the section modules can all reach the option keys, quotas and caps from here.
"""

from __future__ import annotations

from typing import Mapping

from ... import world_pb2 as pb
from .. import items
from ..briefs import Option, TravelState
from ..geometry import Coord
from ..worldmodel import BLOCKING_OBJECT_TYPES, ObjectInfo
from .. import actions

MAX_OPTIONS = 40
WAIT = "wait"

# Walking. Every walk option is a single A* step that also sets the travel
# state, so following a target is "pick the same key again" or `KEEP_GOING`.
STEP_KEY_PREFIX = "step_towards:"
KEEP_GOING = "keep_going"
STOP_GOING = "stop_going"

# How many walk targets each object group may contribute. A per-group quota
# replaces the old shared top-6: in a grove the six nearest objects were all
# trees, so the berry bush eight tiles away and the rock the brief named were
# never offered and Jev could only flip move_N/move_S.
STEP_TARGETS_PER_GROUP = 2
# Other settlers worth a walk option; every living wolf in view gets one.
STEP_SETTLER_LIMIT = 3

# Jev sees a capped list, so the rich categories get their own budget: without
# these caps a settler carrying planks and stone would drown the move options
# in twelve craft lines and eight place lines.
CRAFT_OPTION_LIMIT = 6
PLACE_OPTION_LIMIT = 4

RECIPES = items.RECIPES
# recipe -> {item kind: units consumed}; kept for callers that only want inputs.
CRAFT_RECIPES: Mapping[str, Mapping[str, int]] = items.CRAFT_RECIPES

WIELDABLE_KINDS: frozenset[str] = items.WIELDABLE_KINDS
PLACEABLE_KINDS: frozenset[str] = items.PLACEABLE_KINDS
# A sign is only worth anything with a line on it, and Jev has no way to write
# one: it would plant blank posts. Placing signs is the planner's `place_sign`.
JEV_PLACEABLE_KINDS: frozenset[str] = PLACEABLE_KINDS - frozenset({items.SIGN})

# How far away a wolf counts as "here" for the planner's alert.
WOLF_ALERT_RADIUS = 8

# Shouts. The planner writes the phrases into the brief; Jev only picks among
# them. The cooldown keeps twelve settlers from filling every ear, and a heard
# shout stays worth walking toward for a limited time.
SHOUT_COOLDOWN_TICKS = actions.SHOUT_COOLDOWN_TICKS
MAX_BRIEF_SHOUTS = 4
MAX_SHOUT_LENGTH = actions.MAX_SHOUT_LENGTH
SHOUT_KEY_PREFIX = "shout:"
HEARD_SHOUT_MAX_AGE_TICKS = 20
HEARD_SHOUT_KEY_PREFIX = f"{STEP_KEY_PREFIX}shout:"

# Conversations. Joining one is a seat at a turn-taking table; the walk to a
# free tile next to the anchor is code-owned, like the heard-shout walk.
JOIN_CONVERSATION_KEY_PREFIX = "join_conversation:"
JOIN_CONVERSATION_OPTION_LIMIT = 2

# Hails (docs/09 section 9): addressing a settler next to you starts a
# conversation with the two of you, without either of you having asked first.
# The planner grants them one at a time, in the brief; Jev never invents one.
HAIL_KEY_PREFIX = "hail:"


# Object groups worth walking across the map for, each with its own quota, in
# the order they are offered. Grouping is what stops one dense resource from
# owning every walk option: a grove of trees now spends two slots, not six.
STEP_GROUPS: tuple[tuple[str, frozenset[str]], ...] = (
    ("berry bush", frozenset({items.BUSH})),
    ("tree", frozenset({items.TREE})),
    ("rock", items.ROCK_TYPES),
    ("reeds", frozenset({items.REEDS})),
    ("clay", frozenset({items.CLAY_DEPOSIT})),
    ("copper vein", frozenset({items.COPPER_VEIN})),
    ("iron vein", frozenset({items.IRON_VEIN})),
    ("chest", frozenset({items.CHEST})),
    ("item pile", frozenset({items.ITEM_PILE})),
    ("message board", frozenset({items.MESSAGE_BOARD})),
    ("sign", frozenset({items.SIGN})),
    ("workshop table", frozenset({items.WORKSHOP_TABLE})),
    ("furnace", frozenset({items.FURNACE})),
    ("anvil", frozenset({items.ANVIL})),
    ("bed", frozenset({items.BED})),
)

# Every object type any group covers; used to look candidates up in one pass.
STEP_TARGET_TYPES: frozenset[str] = frozenset(
    object_type for _, types in STEP_GROUPS for object_type in types
)

# The sections an option list is made of, in the order they are offered.
# `MAX_OPTIONS` truncates the tail, so the quota walk targets come last: they
# are the only section that may be cut. A target the brief named, a survival
# action and an interaction with something underfoot always survive.
OPTION_SECTIONS: tuple[str, ...] = (
    "wait",
    "brief_steps",
    "survival",
    "hail",
    "conversation",
    "travel_control",
    "interaction",
    "craft",
    "move",
    "quota_steps",
)


def _move(direction: pb.Direction) -> pb.Intent:
    return pb.Intent(move=pb.MoveIntent(direction=direction))


def _wait_option() -> Option:
    return Option(
        key=WAIT,
        description="do nothing this tick and hold position",
        intent=pb.Intent(wait=pb.WaitIntent()),
    )


def _object_label(obj: ObjectInfo, origin: Coord) -> str:
    dx = obj.position[0] - origin[0]
    dy = obj.position[1] - origin[1]
    return f"{obj.object_type} at dx {dx} dy {dy}"


def travel_state_for(obj: ObjectInfo) -> TravelState:
    """The journey that walks the actor to (or up against) `obj`."""
    return TravelState(
        target=obj.position,
        label=f"{obj.object_id} ({obj.object_type})",
        stop_adjacent=obj.object_type in BLOCKING_OBJECT_TYPES,
    )
