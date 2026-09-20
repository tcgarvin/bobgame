"""Why a stint ends: the thresholds, the reason strings and their explanations.

The end reasons are contract (docs/05): they go into the stint trace, the
planner's report and `tools/analyze_run.py`, so they live in one module nothing
else in the package depends on.
"""

from __future__ import annotations

from typing import Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from ..worldmodel import WorldModel

# Code rules layered on top of Jev's answers. The stint ends when Jev's `done`,
# `stuck` or `lost` probability reaches the threshold on two consecutive ticks.
DONE_OR_STUCK_THRESHOLD = 0.6
LOST_THRESHOLD = 0.6
EJECT_STREAK_TO_END = 2
DANGER_THRESHOLD = 0.8
DANGER_HEALTH_FLOOR = 6
REPEATED_FAILURE_LIMIT = 3

END_SUCCESS_OR_JUDGEMENT = "eject"
# Jev judged that the brief needs something its state does not have: a target
# out of view with no step option toward it, an item it cannot get, a place it
# does not know the way to. The planner has to name or approach it, not retry.
END_LOST = "lost"
LOST_EXPLANATION = (
    "Jev could not see or reach what the brief asked for. Name the target by "
    "object id or as a place in `places`, or move closer first."
)
# The world's intent deadline is 1200 ms after tick start; leave room for the
# gRPC round trip and the state build.
JEV_TICK_BUDGET_SECONDS = 0.9
# Every target the brief named is unreachable: A* finds no route to any of the
# places or object ids it mentions, several ticks running. A settler once spent
# 108 ticks stepping north and south inside a pocket of her own walls, toward a
# bush four tiles away with no way round; ending promptly and naming the target
# is what lets the planner do something else (docs: hamlet round 2).
END_NO_PATH = "no_path"
NO_PATH_PATIENCE = 3
NO_PATH_EXPLANATION = (
    "No route to what the brief named ({targets}) for {ticks} ticks running: "
    "every path is blocked. Nothing about the body or the brief changes that "
    "from here."
)

# Food crossed down past a threshold during the stint. Crossing only: a stint
# that starts below the line is not ended on its first tick.
END_FOOD_LOW = "food_low"
END_FOOD_ZERO = "food_zero"
FOOD_LOW_EXPLANATION = (
    "Food crossed down to {food}/{max_food} at tick {tick} (it was {before} "
    "when this stint started). Food falls 1 every {interval} ticks; at 0 you "
    "lose {damage} health every {starve} ticks. One berry restores {berry}."
)
FOOD_ZERO_EXPLANATION = (
    "Food reached 0/{max_food} at tick {tick}. You lose {damage} health every "
    "{starve} ticks until you eat; health is {health}/{max_health}. One berry "
    "restores {berry}."
)

END_TICKS = "ticks_exhausted"
END_DEATH = "death"
END_REPEATED_FAILURE = "repeated_failure"
END_CANCELLED = "cancelled"
# The body fell asleep, so nothing the stint asks for can reach the world. A
# new-moon night is the case where the world chose it rather than the settler
# (docs/14 section 1).
END_ASLEEP = "asleep"
END_NEW_MOON = "new_moon"
SLEEP_END_EXPLANATIONS: Mapping[str, str] = {
    END_ASLEEP: (
        "The body fell asleep at tick {tick}, so this stint ended there; a "
        "sleeper submits nothing."
    ),
    END_NEW_MOON: (
        "The new moon put everyone on the island to sleep at tick {tick}, so "
        "this stint ended there; a sleeper submits nothing."
    ),
}

# A reflex stint pre-empted this one (docs/09 section 4.2).
END_PREEMPTED_BY_REFLEX = "reflex"
# The actor took a seat in a conversation, which owns the body from now on.
END_JOINED_CONVERSATION = "joined_conversation"

# `kind` on the `stint_start` trace line: what sort of stint this is.
STINT_KIND_ORDINARY = "stint"
STINT_KIND_REFLEX = "reflex"


def never_ends(model: "WorldModel") -> str:
    """The default extra end rule: no stint ends because of it."""
    return ""
