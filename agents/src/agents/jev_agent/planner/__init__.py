"""The slow half of the actor: a pydantic-ai agent that decides what to do next.

The planner never touches the tick loop. It looks at the world model, writes to
its memory file, and mostly hands control to Jev through `start_stint`, which
only returns once the stint has ended. Single-tick tools exist for the fiddly
moments (craft this, place that) where a whole stint would be overkill.

Inside the package, bottom first: `common` (the few shared names), `prompt`
(the system prompt), `status` (the clock and `!!` lines), `validation` (what a
bad tool argument is told), `describe` (`look`), `toolset` (the budget and the
wrapper), `tools/` (one module per tool family), `factory` (registration order)
and `turn` (the `Planner` loop).

Patching note: the turn budget lives in `planner.toolset`, the retry delay in
`planner.turn`, and the build resupply rounds in `planner.tools.building`.
Patch the module that owns a constant, not this re-export.
"""

from __future__ import annotations

from .common import (
    FATIGUE_ALERT_MARGIN,
    FOOD_ALERT_AT,
    MAX_TOOL_CALLS_PER_TURN,
    RECENT_ATTACK_TICKS,
    WOLF_ENTITY_TYPE,
)
from .describe import (
    BUILD_SITE_TYPES,
    BULK_OBJECT_TYPES,
    BUSHES_SHOWN,
    PILES_SHOWN,
    SIGNS_SHOWN,
    berry_bush_lines,
    describe_world,
    pile_lines,
)
from .factory import build_planner_agent
from .prompt import (
    DEATH_NOTE,
    SETTLEMENT_NARRATIVE,
    SLEEP_NOTE,
    TURN_ENDS_AFTER_SLEEP,
    settlement_narrative,
)
from .status import (
    HAND_BACK_ADVICE,
    alert_window_start,
    body_alerts,
    threat_alert,
    turn_clock_line,
)
from .toolset import (
    BUDGET_SPENT_MESSAGE,
    HARD_LIMIT_MARGIN,
    PLANNER_TOOL_RETRIES,
    TOOL_BUDGET_WARNING_AT,
    BudgetedToolset,
    PlannerDeps,
    ToolBudget,
)
from .tools.building import BUILD_CRAFT_LIMIT, BUILD_RESUPPLY_ROUNDS
from .tools.core import (
    DESTINATION_PLACE,
    TRAVEL_ARRIVED,
    TRAVEL_ARRIVED_NEXT_TO,
    TRAVEL_MAX_TICKS,
    TRAVEL_MIN_TICKS,
    TRAVEL_TICKS_PER_STEP,
    TRAVEL_TICK_ALLOWANCE,
    travel_arrival,
    travel_arrival_text,
    travel_budget,
)
from .tools.memory import read_memory
from .turn import (
    HISTORY_MESSAGE_LIMIT,
    STINT_REPORTS_KEPT,
    TURN_RETRY_SECONDS,
    Planner,
    trim_history,
)

# Crafting is `recipes.py`'s; these names are re-exported for readers that
# knew them here.
from ..outcomes import CRAFTED_DETAIL
from ..recipes import (
    CRAFT_ACTION_MARGIN,
    CRAFT_CHAIN_DEPTH,
    SOURCES_SHOWN,
    CraftTally,
    carried,
    craft_chain,
    craft_once,
    missing_input_lines,
    source_lines,
    stock_one,
)
from .validation import (
    direction_value,
    parse_tile_list,
    refuse_dead_targets,
    settlers_met,
    validated_hails,
    validated_places,
    validated_shouts,
)

__all__ = [
    "BUDGET_SPENT_MESSAGE",
    "CRAFTED_DETAIL",
    "CRAFT_ACTION_MARGIN",
    "CRAFT_CHAIN_DEPTH",
    "CraftTally",
    "SOURCES_SHOWN",
    "TRAVEL_ARRIVED",
    "TRAVEL_ARRIVED_NEXT_TO",
    "TRAVEL_MAX_TICKS",
    "TRAVEL_MIN_TICKS",
    "TRAVEL_TICKS_PER_STEP",
    "TRAVEL_TICK_ALLOWANCE",
    "carried",
    "craft_chain",
    "craft_once",
    "missing_input_lines",
    "source_lines",
    "stock_one",
    "BUILD_CRAFT_LIMIT",
    "BUILD_RESUPPLY_ROUNDS",
    "BUILD_SITE_TYPES",
    "BULK_OBJECT_TYPES",
    "BUSHES_SHOWN",
    "BudgetedToolset",
    "DEATH_NOTE",
    "DESTINATION_PLACE",
    "FATIGUE_ALERT_MARGIN",
    "FOOD_ALERT_AT",
    "HAND_BACK_ADVICE",
    "HARD_LIMIT_MARGIN",
    "HISTORY_MESSAGE_LIMIT",
    "MAX_TOOL_CALLS_PER_TURN",
    "PILES_SHOWN",
    "PLANNER_TOOL_RETRIES",
    "Planner",
    "PlannerDeps",
    "RECENT_ATTACK_TICKS",
    "SETTLEMENT_NARRATIVE",
    "SIGNS_SHOWN",
    "SLEEP_NOTE",
    "STINT_REPORTS_KEPT",
    "TOOL_BUDGET_WARNING_AT",
    "TURN_ENDS_AFTER_SLEEP",
    "TURN_RETRY_SECONDS",
    "ToolBudget",
    "WOLF_ENTITY_TYPE",
    "alert_window_start",
    "berry_bush_lines",
    "body_alerts",
    "build_planner_agent",
    "describe_world",
    "direction_value",
    "parse_tile_list",
    "pile_lines",
    "read_memory",
    "refuse_dead_targets",
    "settlement_narrative",
    "settlers_met",
    "threat_alert",
    "travel_arrival",
    "travel_arrival_text",
    "travel_budget",
    "trim_history",
    "turn_clock_line",
    "validated_hails",
    "validated_places",
    "validated_shouts",
]
