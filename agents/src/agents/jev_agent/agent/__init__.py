"""The agent process: one tick loop, one planner task, one entity.

The tick loop owns the world connection and submits exactly one intent per tick,
before the deadline. The planner runs as a background task and reaches the tick
loop only through the handshakes on `JevAgent`, each of which parks the planner
on an asyncio Future until the tick loop has actually done the thing.

Inside the package: `modes` (the mode enum and the loop's timings, with the
transition table), `sleeping` (the world's sleep wording, the finished record
and the parked `sleep` tools), `saving` (where a snapshot goes and what
"drained" means), `requests` (what a planner tool hands the loop), `status`
(the viewer's status line) and `core` (`JevAgent` itself).

`JevAgent` stays one class: its per-tick priority order lives in one place, and
the parts that look extractable - the sleep transition, the drain, the
conversation phase - each reach a dozen of its fields.
"""

from __future__ import annotations

from .core import JevAgent, run_agent
from .modes import (
    CONVERSATION_START_GRACE_TICKS,
    LIFE_RESPAWNED,
    LIFE_WOKE,
    MAX_PLANNER_WAIT_MS,
    MODE_CONVERSATION,
    MODE_IDLE,
    MODE_PLANNING,
    MODE_REFLEX,
    MODE_STINT,
    PLANNER_POLL_SECONDS,
    SUBMIT_MARGIN_MS,
    THOUGHT_CHANNEL,
    USE_RUN_DIR,
    Mode,
    ResultSink,
)
from .requests import (
    ASLEEP_REJECTION,
    _ConversationWaiter,
    _DirectRequest,
    _HeldStint,
    _StintRequest,
)
from .saving import (
    DEFAULT_SAVE_WAIT_SECONDS,
    DRAINED,
    SAVES_DIR_NAME,
    SAVE_POLL_SECONDS,
    SAVE_WAIT_ENV,
    DrainState,
    save_wait_seconds,
)
from .sleeping import (
    COLLAPSE_ACTION,
    GROUND_SLEEP_PLACE,
    SLEEP_ACTION,
    SLEEP_START_GRACE_TICKS,
    UNKNOWN_WAKE_REASON,
    WAKE_ACTION,
    WAKE_HUNGRY_REASON,
    SleepRecord,
    WakeWaiters,
    sleep_place,
    wake_reason,
)
from .status import StatusLine

__all__ = [
    "ASLEEP_REJECTION",
    "COLLAPSE_ACTION",
    "CONVERSATION_START_GRACE_TICKS",
    "DEFAULT_SAVE_WAIT_SECONDS",
    "DRAINED",
    "DrainState",
    "GROUND_SLEEP_PLACE",
    "JevAgent",
    "LIFE_RESPAWNED",
    "LIFE_WOKE",
    "MAX_PLANNER_WAIT_MS",
    "MODE_CONVERSATION",
    "MODE_IDLE",
    "MODE_PLANNING",
    "MODE_REFLEX",
    "MODE_STINT",
    "Mode",
    "PLANNER_POLL_SECONDS",
    "ResultSink",
    "SAVES_DIR_NAME",
    "SAVE_POLL_SECONDS",
    "SAVE_WAIT_ENV",
    "SLEEP_ACTION",
    "SLEEP_START_GRACE_TICKS",
    "SUBMIT_MARGIN_MS",
    "SleepRecord",
    "StatusLine",
    "THOUGHT_CHANNEL",
    "UNKNOWN_WAKE_REASON",
    "USE_RUN_DIR",
    "WAKE_ACTION",
    "WAKE_HUNGRY_REASON",
    "WakeWaiters",
    "run_agent",
    "save_wait_seconds",
    "sleep_place",
    "wake_reason",
]
