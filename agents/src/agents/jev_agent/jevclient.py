"""The TypeSafe System One call that drives a stint, and its interface.

One request per tick asks five questions about one state: which action to take
(`Choice` over the code-enumerated legal options), whether the brief's success
condition is met (`Noul`), whether the brief has become impossible or needs a
judgement it does not cover (`Noul`), whether the state is missing something the
brief needs (`Noul`), and whether the actor is about to die (`Noul`).

`done` and `stuck` used to be one bundled question, and bundling cost the stint
its ending: asked about success *or* impossibility *or* judgement at once, Jev
answered 0.5-0.6 on a plainly finished job, under the threshold, and thirteen
stints in one run burned their whole tick budget after the job was done.
`lost` is split out of `stuck` for the same reason in the other direction: an
unreachable target does not feel impossible to Jev, so it sat at stuck 0.4 for
30 ticks while stepping north and south towards a bush it had no option to
reach; asked concretely about a missing target, item or route it can say so.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

import structlog
from typesafe_sdk import (
    AsyncTypeSafeClient,
    Choice,
    ChoiceAnswer,
    Noul,
    NoulAnswer,
    RetryPolicy,
)

logger = structlog.get_logger(__name__)

DEFAULT_MODEL = "jev-latest"
DEFAULT_TIMEOUT_SECONDS = 2.5

ACTION_QUESTION = (
    "You are controlling a settler in a survival world. Given the state, which "
    "single action should the settler take this tick to make progress on the "
    "brief while staying alive?"
)
DONE_QUESTION = (
    "Is the brief's success condition met right now, as far as the state shows?"
)
STUCK_QUESTION = (
    "Has the brief become impossible, or does the situation need a judgement the "
    "brief does not cover? Being far from a target that has a step option "
    "toward it is neither."
)
LOST_QUESTION = (
    "Is progress on the brief impossible from here because something it needs "
    "is missing from this state: a target that is not on the map or that no "
    "step option leads toward, or an item or station the brief needs that is "
    "not here and cannot be got here? A named place with a step option toward "
    "it is not missing, even when it is far away."
)
DANGER_QUESTION = (
    "Is this settler in immediate danger of dying within the next few ticks?"
)


@dataclass(frozen=True)
class JevDecision:
    """One tick's answer from Jev."""

    action: str
    probabilities: Mapping[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    done: float = 0.0
    stuck: float = 0.0
    lost: float = 0.0
    danger: float = 0.0
    input_tokens: int = 0
    latency_ms: int = 0

    @property
    def eject(self) -> float:
        """How strongly Jev wants the planner back: the highest of the three.

        Derived, so traces, reports and the viewer keep the single number they
        have always shown.
        """
        return max(self.done, self.stuck, self.lost)

    def top(self, count: int = 3) -> list[tuple[str, float]]:
        """The `count` most likely options, most likely first."""
        ranked = sorted(self.probabilities.items(), key=lambda kv: -kv[1])
        return ranked[:count]


class JevClient(Protocol):
    """What a stint needs from Jev. Tests substitute a scripted fake."""

    async def decide(
        self, state: Mapping[str, Any], options: Mapping[str, str]
    ) -> JevDecision:
        """Choose one of `options` given `state`."""


class TypeSafeJevClient:
    """`JevClient` backed by the TypeSafe System One API."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = 2,
    ) -> None:
        self._client = AsyncTypeSafeClient(
            model=model,
            retry=RetryPolicy(max_retries=max_retries),
            timeout=timeout,
        )

    async def decide(
        self, state: Mapping[str, Any], options: Mapping[str, str]
    ) -> JevDecision:
        """Ask Jev for an action and the `done`, `stuck`, `lost` and `danger` nouls."""
        if not options:
            raise ValueError("Jev needs at least one option to choose from")
        started = time.monotonic()
        response = await self._client.system_one(
            dict(state),
            {
                "action": Choice(instructions=ACTION_QUESTION, criteria=dict(options)),
                "done": Noul(instructions=DONE_QUESTION),
                "stuck": Noul(instructions=STUCK_QUESTION),
                "lost": Noul(instructions=LOST_QUESTION),
                "danger": Noul(instructions=DANGER_QUESTION),
            },
        )
        latency_ms = int((time.monotonic() - started) * 1000)

        action = response.answers["action"]
        if not isinstance(action, ChoiceAnswer):
            raise TypeError(f"expected a ChoiceAnswer for 'action', got {type(action)}")
        return JevDecision(
            action=action.choice,
            probabilities=dict(action.probabilities),
            confidence=action.confidence,
            done=_noul(response.answers.get("done")),
            stuck=_noul(response.answers.get("stuck")),
            lost=_noul(response.answers.get("lost")),
            danger=_noul(response.answers.get("danger")),
            input_tokens=response.usage.input_tokens or 0,
            latency_ms=latency_ms,
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()


def _noul(answer: object) -> float:
    if isinstance(answer, NoulAnswer):
        return answer.noul
    return 0.0
