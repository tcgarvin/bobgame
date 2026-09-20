"""The handful of names more than one planner module needs.

The bottom of the package: it imports nothing from its siblings.
"""

from __future__ import annotations

from .. import items

MAX_TOOL_CALLS_PER_TURN = 20

# `EntityInfo.entity_type` for a wolf, which is neither a settler nor hailable.
WOLF_ENTITY_TYPE = "wolf"

# A new turn opens with an alert when the actor was bitten this recently.
RECENT_ATTACK_TICKS = 5

# Food at or below this gets its own `!!` line on every tool result: a turn can
# run for hundreds of ticks, and food falls one point every four of them. The
# number lives in `items.py` because `stint` ends a stint on the same line.
FOOD_ALERT_AT = items.FOOD_ALERT_AT
# Fatigue within this much of `items.PLAYER_MAX_FATIGUE` gets the same treatment.
FATIGUE_ALERT_MARGIN = 10
