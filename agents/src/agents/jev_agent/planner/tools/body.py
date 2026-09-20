"""Keeping the body going: eat, sleep and wake."""

from __future__ import annotations

from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from .... import world_pb2 as pb
from ... import items
from ...actions import collect_here_attempt, eat_attempt, eat_now, sleep_attempt
from ..prompt import TURN_ENDS_AFTER_SLEEP
from ..describe import BUSHES_SHOWN, berry_bush_lines
from ..toolset import PlannerDeps
from .common import run_attempt


def register_eat(tools: FunctionToolset[PlannerDeps]) -> None:
    """eat: one item from the pack, or the berry underfoot."""

    @tools.tool
    async def eat(ctx: RunContext[PlannerDeps], kind: str = "berry") -> str:
        """Eat one item from your pack to restore food.

        A berry restores 20 food and costs one tick. With no berry in your
        pack: if you are standing on a bush that has one, this picks it and
        eats it, which is two actions and two ticks, and the result reports
        both. Otherwise nothing is submitted, no tick is spent, and the result
        names the nearest bushes you know of that had a berry when you last
        saw them.

        Args:
            kind: what to eat; `berry` is the only food in the world.
        """
        bridge = ctx.deps.bridge
        from_pack = eat_attempt(bridge.model, kind)
        if from_pack.allowed:
            return (await run_attempt(ctx, from_pack)).text()
        if kind == items.BERRY:
            pick = collect_here_attempt(bridge.model)
            if pick.allowed:
                picked = await run_attempt(ctx, pick)
                if not picked.ok:
                    return picked.text()
                eaten = await run_attempt(ctx, eat_now(kind))
                return f"{picked.text()}\n{eaten.text()}"
        lines = [f"you carry no {kind}, and there is none to pick where you stand"]
        bushes = berry_bush_lines(bridge.model, BUSHES_SHOWN)
        if bushes:
            lines.append("bushes that had a berry when you last saw them:")
            lines.extend(bushes)
        return "\n".join(lines)


def register_sleep(tools: FunctionToolset[PlannerDeps]) -> None:
    """sleep and wake."""

    @tools.tool
    async def sleep(ctx: RunContext[PlannerDeps], bed: str = "") -> str:
        """Lie down and sleep; the call returns when you wake, and says why.

        Args:
            bed: the id of a bed on or next to your tile. Leave it empty
                (the default) to sleep on the ground where you stand.
        """
        bridge = ctx.deps.bridge
        attempt = sleep_attempt(bridge.model, bed)
        if not attempt.allowed:
            return attempt.refusal
        since_tick = bridge.model.tick
        outcome = await bridge.direct_action(attempt.intent, attempt.description)
        if not outcome.ok:
            return outcome.text()
        slept = await bridge.await_wake(since_tick)
        woke = slept or "you did not stay asleep"
        # The turn is over whatever the model wanted next: the journal rewrite
        # started when the body lay down, and this turn's history is stale.
        ctx.deps.budget.spend()
        return f"{outcome.text()}\n{woke}\n{TURN_ENDS_AFTER_SLEEP}"

    @tools.tool
    async def wake(ctx: RunContext[PlannerDeps]) -> str:
        """Stop sleeping. While you are asleep this is the only thing you can do."""
        if not ctx.deps.bridge.model.self_info.asleep:
            return "you are not asleep"
        outcome = await ctx.deps.bridge.direct_action(
            pb.Intent(wake=pb.WakeIntent()), "wake up"
        )
        return outcome.text()
