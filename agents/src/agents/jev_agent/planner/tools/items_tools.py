"""Moving items around: piles, chests and handing things to another settler."""

from __future__ import annotations

from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from ...actions import (
    deposit_attempt,
    drop_attempt,
    give_attempt,
    pickup_attempt,
    withdraw_attempt,
)
from ..describe import PILES_SHOWN, pile_lines
from ..toolset import PlannerDeps
from .common import run_attempt, attempt_text


def register_containers(tools: FunctionToolset[PlannerDeps]) -> None:
    """pickup, drop, deposit and withdraw: piles and chests."""

    @tools.tool
    async def pickup(ctx: RunContext[PlannerDeps], kind: str, amount: int = 1) -> str:
        """Take items from the pile your body is standing on.

        A pile is on one tile, and you must be on that tile: `travel_to` its
        position first. If there is no pile under you, the result names the
        nearest piles you know of and what they hold.
        """
        outcome = await run_attempt(
            ctx, pickup_attempt(ctx.deps.bridge.model, kind, amount)
        )
        if outcome.ok:
            return outcome.text()
        piles = pile_lines(ctx.deps.bridge.model, PILES_SHOWN)
        if not piles:
            return outcome.text()
        return "\n".join([outcome.text(), "piles you know of:", *piles])

    @tools.tool
    async def drop(ctx: RunContext[PlannerDeps], kind: str, amount: int = 1) -> str:
        """Drop items onto your tile as a pile."""
        return await attempt_text(
            ctx, drop_attempt(ctx.deps.bridge.model, kind, amount)
        )

    @tools.tool
    async def deposit(
        ctx: RunContext[PlannerDeps], object_id: str, kind: str, amount: int = 1
    ) -> str:
        """Put items into a chest on your tile or next to it."""
        return await attempt_text(
            ctx, deposit_attempt(ctx.deps.bridge.model, object_id, kind, amount)
        )

    @tools.tool
    async def withdraw(
        ctx: RunContext[PlannerDeps], object_id: str, kind: str, amount: int = 1
    ) -> str:
        """Take items out of a chest on your tile or next to it."""
        return await attempt_text(
            ctx, withdraw_attempt(ctx.deps.bridge.model, object_id, kind, amount)
        )


def register_give(tools: FunctionToolset[PlannerDeps]) -> None:
    """give: handing items to another settler."""

    @tools.tool
    async def give(
        ctx: RunContext[PlannerDeps], entity_id: str, kind: str, amount: int = 1
    ) -> str:
        """Hand items to a settler next to you or seated in your conversation.

        Args:
            entity_id: who receives them.
            kind: the item name, exactly as your inventory spells it.
            amount: how many.
        """
        return await attempt_text(
            ctx, give_attempt(ctx.deps.bridge.model, entity_id, kind, amount)
        )
