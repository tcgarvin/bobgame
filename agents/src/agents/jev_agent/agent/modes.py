"""The agent's modes, and the timings the tick loop runs on.

The mode machine, as `core.JevAgent._choose_intent` reads it top-down:

| from         | to             | when                                          |
|--------------|----------------|-----------------------------------------------|
| any          | (asleep)       | the body is asleep: only a `wake` is submitted |
| any but stint| REFLEX         | the reflex trigger fires                       |
| any          | CONVERSATION   | this actor's seat lands in a conversation      |
| IDLE/PLANNING| STINT          | the planner's `start_stint` is picked up       |
| REFLEX       | back to before | the reflex stint ends                          |
| STINT        | PLANNING       | the stint ends and its report is handed back   |
| CONVERSATION | PLANNING       | the seat is gone                               |

The values are contract: they go into the stint trace and the viewer's agent
panel, so `Mode` is a `str` enum and `MODE_*` are aliases for the members.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Callable


class Mode(str, Enum):
    """What owns the body right now."""

    PLANNING = "planning"
    STINT = "stint"
    REFLEX = "reflex"
    CONVERSATION = "conversation"
    IDLE = "idle"


MODE_PLANNING = Mode.PLANNING
MODE_STINT = Mode.STINT
MODE_REFLEX = Mode.REFLEX
MODE_CONVERSATION = Mode.CONVERSATION
MODE_IDLE = Mode.IDLE

# How the tick loop waits for the planner inside a tick (see
# `_await_planner_work`). The margin leaves room for the gRPC submit; the cap
# keeps a clock skew between world and agent from stalling the loop.
SUBMIT_MARGIN_MS = 200.0
MAX_PLANNER_WAIT_MS = 2_000.0
PLANNER_POLL_SECONDS = 0.02

# Called with "accepted" or a rejection string once the world has answered.
ResultSink = Callable[[str], None]

THOUGHT_CHANNEL = "thought"

# How long a planner tool that has just opened or joined a conversation waits
# for the tick loop to see the object before it gives up on it.
CONVERSATION_START_GRACE_TICKS = 4

# The `log_root` an agent is built with when nobody named one: work it out from
# `$BOBGAME_RUN_DIR` (`tracelog.resolve_log_root`). An empty path says "not
# given" without a `None` that also has to mean "the current directory".
USE_RUN_DIR = Path()

# What the planner is told happened to its body, so it can drop its history.
LIFE_WOKE = "woke"
LIFE_RESPAWNED = "respawned"
