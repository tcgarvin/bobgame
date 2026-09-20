"""A stint: the stretch of ticks during which Jev holds the controls.

The planner hands down a `Brief`; this package turns it into one Jev call per
tick, maps the answer onto a proto Intent, enforces the code-owned rules that
sit on top of Jev's judgement, writes a JSONL trace, and finally compresses the
whole run into a `StintReport` the planner can read.

Inside the package: `endings` (thresholds, end reasons and their explanations),
`records` (`TickRecord` and `StintReport`) and `runner` (the `Stint` machine).
"""

from __future__ import annotations

from ..briefs import (
    INTERRUPTED_BY_CONVERSATION,
    INTERRUPTED_BY_REFLEX,
    Brief,
    BriefHail,
    DriverChoice,
    Option,
    StintDriver,
    TravelState,
    conversation_interruption,
    was_interrupted,
)
from .endings import (
    DANGER_HEALTH_FLOOR,
    DANGER_THRESHOLD,
    DONE_OR_STUCK_THRESHOLD,
    EJECT_STREAK_TO_END,
    END_ASLEEP,
    END_CANCELLED,
    END_DEATH,
    END_FOOD_LOW,
    END_FOOD_ZERO,
    END_JOINED_CONVERSATION,
    END_LOST,
    END_NEW_MOON,
    END_NO_PATH,
    END_PREEMPTED_BY_REFLEX,
    END_REPEATED_FAILURE,
    END_SUCCESS_OR_JUDGEMENT,
    END_TICKS,
    FOOD_LOW_EXPLANATION,
    FOOD_ZERO_EXPLANATION,
    JEV_TICK_BUDGET_SECONDS,
    LOST_EXPLANATION,
    LOST_THRESHOLD,
    NO_PATH_EXPLANATION,
    NO_PATH_PATIENCE,
    REPEATED_FAILURE_LIMIT,
    SLEEP_END_EXPLANATIONS,
    STINT_KIND_ORDINARY,
    STINT_KIND_REFLEX,
    never_ends,
)
from .records import StintReport, TickRecord, stint_stats
from .runner import Stint

__all__ = [
    "Brief",
    "BriefHail",
    "DANGER_HEALTH_FLOOR",
    "DANGER_THRESHOLD",
    "DONE_OR_STUCK_THRESHOLD",
    "DriverChoice",
    "EJECT_STREAK_TO_END",
    "END_ASLEEP",
    "END_CANCELLED",
    "END_DEATH",
    "END_FOOD_LOW",
    "END_FOOD_ZERO",
    "END_JOINED_CONVERSATION",
    "END_LOST",
    "END_NEW_MOON",
    "END_NO_PATH",
    "END_PREEMPTED_BY_REFLEX",
    "END_REPEATED_FAILURE",
    "END_SUCCESS_OR_JUDGEMENT",
    "END_TICKS",
    "FOOD_LOW_EXPLANATION",
    "FOOD_ZERO_EXPLANATION",
    "INTERRUPTED_BY_CONVERSATION",
    "INTERRUPTED_BY_REFLEX",
    "JEV_TICK_BUDGET_SECONDS",
    "LOST_EXPLANATION",
    "LOST_THRESHOLD",
    "NO_PATH_EXPLANATION",
    "NO_PATH_PATIENCE",
    "Option",
    "REPEATED_FAILURE_LIMIT",
    "SLEEP_END_EXPLANATIONS",
    "STINT_KIND_ORDINARY",
    "STINT_KIND_REFLEX",
    "Stint",
    "StintDriver",
    "StintReport",
    "TickRecord",
    "TravelState",
    "conversation_interruption",
    "never_ends",
    "stint_stats",
    "was_interrupted",
]
