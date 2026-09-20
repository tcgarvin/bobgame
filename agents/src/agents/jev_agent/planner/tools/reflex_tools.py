"""Registering and clearing the reflex brief code runs when a wolf turns up."""

from __future__ import annotations

from typing import Mapping, Sequence

from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from ...reflex import ReflexBrief, clamp_trigger_distance
from ..toolset import PlannerDeps
from ..validation import refuse_dead_targets, validated_places, validated_shouts


def register(tools: FunctionToolset[PlannerDeps]) -> None:
    """Register these tools on the planner's toolset."""

    @tools.tool
    async def set_reflex(
        ctx: RunContext[PlannerDeps],
        instruction: str,
        success_condition: str,
        max_ticks: int,
        trigger_distance: int,
        notes: str = "",
        shouts: Sequence[str] = (),
        places: Mapping[str, Sequence[int]] = {},
    ) -> str:
        """Register the brief code runs for you when a wolf is near or you are hit.

        It replaces any reflex you had, it is kept across turns, and it fires
        while you are thinking, while a single-tick tool or a wait is in
        flight, while you are in a conversation, and during build or
        travel_to. It never interrupts a start_stint.

        Args:
            instruction: what Jev should do, concretely, when it fires.
            success_condition: what Jev should be able to see when it is done.
            max_ticks: tick budget for the reflex stint.
            trigger_distance: how close a living wolf must be to start it, 1 to 8
                tiles. Damage from an attacker starts it whatever the distance.
            notes: extra hints for Jev, as in start_stint.
            shouts: the exact phrases Jev may shout while the reflex runs.
                Good for raising the alarm or calling for help the moment the
                reflex fires; nobody can answer it.
            places: named map positions Jev may walk to, as in start_stint.
        """
        refuse_dead_targets(
            ctx.deps.bridge.model, instruction, success_condition, notes
        )
        brief = ReflexBrief(
            instruction=instruction,
            success_condition=success_condition,
            max_ticks=max(1, max_ticks),
            trigger_distance=clamp_trigger_distance(trigger_distance),
            notes=notes,
            shouts=validated_shouts(shouts),
            places=validated_places(places, ctx.deps.bridge.model),
        )
        ctx.deps.bridge.set_reflex(brief)
        return f"reflex registered: {brief.prompt_line()}"

    @tools.tool
    async def clear_reflex(ctx: RunContext[PlannerDeps]) -> str:
        """Remove your reflex brief; nothing runs for you until you set another."""
        ctx.deps.bridge.clear_reflex()
        return "reflex cleared"
