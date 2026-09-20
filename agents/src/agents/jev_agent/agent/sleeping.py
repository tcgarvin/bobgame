"""The vocabulary of sleep, and the tools parked until the body wakes.

Deciding *that* the body fell asleep, and everything that follows from it, is
`core.JevAgent`'s: the transition touches the trace, the day log, the planner's
turn, the journal rewrite and every holder of the body, which is far more than
a collaborator could reach for. What lives here is what stands on its own: the
world's wording, the finished record, and the waiting `sleep` tools.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from .. import items
from ..worldmodel import TickDigest

# How long the `sleep` tool waits for the tick loop to see the actor asleep
# before it decides the sleep never happened.
SLEEP_START_GRACE_TICKS = 3

# The world's own wording for falling asleep, collapsing and waking.
SLEEP_ACTION = "sleep"
COLLAPSE_ACTION = "collapse"
WAKE_ACTION = "wake"
GROUND_SLEEP_PLACE = "the ground"
UNKNOWN_WAKE_REASON = "unknown"
# The world's word for "your food fell to the wake threshold" (`world/sleep.py`,
# WAKE_HUNGRY). The sleep report spells the numbers out when it sees it.
WAKE_HUNGRY_REASON = "hungry"


@dataclass(frozen=True)
class SleepRecord:
    """One completed sleep, as the planner's `sleep` tool reports it."""

    start_tick: int
    end_tick: int
    reason: str
    fatigue_before: int
    fatigue_after: int
    where: str
    food_after: int = 0

    @property
    def ticks_slept(self) -> int:
        """World ticks between falling asleep and waking."""
        return max(0, self.end_tick - self.start_tick)

    def to_text(self) -> str:
        """The line the `sleep` tool returns."""
        line = (
            f"slept on {self.where} from tick {self.start_tick} to "
            f"{self.end_tick} ({self.ticks_slept} ticks); woke because "
            f"{self.reason}; fatigue {self.fatigue_before} -> {self.fatigue_after}"
        )
        if self.reason == WAKE_HUNGRY_REASON:
            line += (
                f"; food is {self.food_after} and a sleeper wakes at food "
                f"{items.HUNGRY_WAKE_FOOD}, which is also the level below "
                "which you cannot fall asleep at all"
            )
        return line


def sleep_place(digest: TickDigest) -> str:
    """Where the world says the actor fell asleep, from its own action event."""
    for acted in digest.own_actions:
        if acted.action_type in (SLEEP_ACTION, COLLAPSE_ACTION) and acted.success:
            return acted.details or GROUND_SLEEP_PLACE
    return GROUND_SLEEP_PLACE


def wake_reason(digest: TickDigest) -> str:
    """Why the actor woke, from the world's `wake` action event."""
    for acted in digest.own_actions:
        if acted.action_type == WAKE_ACTION and acted.success:
            return acted.details or UNKNOWN_WAKE_REASON
    return UNKNOWN_WAKE_REASON


@dataclass
class WakeWaiter:
    """A planner `sleep` tool parked until the actor is awake again."""

    # The rendered sleep, or "" when no sleep was seen.
    future: asyncio.Future[str]
    deadline_tick: int


@dataclass
class WakeWaiters:
    """Every parked `sleep` tool, and the two ways one is let go."""

    waiting: list[WakeWaiter] = field(default_factory=list)

    def __bool__(self) -> bool:
        """Whether any `sleep` tool is parked."""
        return bool(self.waiting)

    def park(self, deadline_tick: int) -> asyncio.Future[str]:
        """Park a tool until the body wakes or `deadline_tick` passes."""
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self.waiting.append(WakeWaiter(future, deadline_tick))
        return future

    def release(self, text: str) -> None:
        """Answer every parked `sleep` tool with this sleep (or with nothing)."""
        for waiter in list(self.waiting):
            self.waiting.remove(waiter)
            if not waiter.future.done():
                waiter.future.set_result(text)

    def expire(self, tick: int) -> None:
        """Release a `sleep` tool whose sleep never started."""
        for waiter in list(self.waiting):
            if tick < waiter.deadline_tick:
                continue
            self.waiting.remove(waiter)
            if not waiter.future.done():
                waiter.future.set_result("")
