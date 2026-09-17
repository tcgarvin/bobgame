"""The TypeSafe System One call that drives a stint, and its interface.

One request per tick asks three questions about one state: which action to take
(`Choice` over the code-enumerated legal options), whether to hand control back
to the planner (`Noul`), and whether the actor is about to die (`Noul`).
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
EJECT_QUESTION = (
    "Should control return to the slow planner now, because the brief's success "
    "condition is met, the brief has become impossible, or the situation needs "
    "judgement the brief does not cover?"
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
    eject: float = 0.0
    danger: float = 0.0
    input_tokens: int = 0
    latency_ms: int = 0

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
        """Ask Jev for an action, an eject probability, and a danger probability."""
        if not options:
            raise ValueError("Jev needs at least one option to choose from")
        started = time.monotonic()
        response = await self._client.system_one(
            dict(state),
            {
                "action": Choice(instructions=ACTION_QUESTION, criteria=dict(options)),
                "eject": Noul(instructions=EJECT_QUESTION),
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
            eject=_noul(response.answers.get("eject")),
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
