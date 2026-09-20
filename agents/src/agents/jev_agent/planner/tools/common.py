"""Submitting one attempt and rendering what the world did with it."""

from __future__ import annotations

from pydantic_ai import RunContext

from ...actions import Attempt
from ...outcomes import ActionOutcome
from ..toolset import PlannerDeps


async def run_attempt(ctx: RunContext[PlannerDeps], attempt: Attempt) -> ActionOutcome:
    """Submit an allowed attempt; a refused one never reaches the world.

    A refused attempt is rendered as the sentence `actions.py` wrote, with no
    arrow and no world action, because nothing was submitted.
    """
    if not attempt.allowed:
        return ActionOutcome(description=attempt.description, not_run=attempt.refusal)
    return await ctx.deps.bridge.direct_action(attempt.intent, attempt.description)


async def attempt_text(ctx: RunContext[PlannerDeps], attempt: Attempt) -> str:
    """The tool result for an attempt: its refusal, or what the world did."""
    if not attempt.allowed:
        return attempt.refusal
    outcome = await ctx.deps.bridge.direct_action(attempt.intent, attempt.description)
    return outcome.text()
