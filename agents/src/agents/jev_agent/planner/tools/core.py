"""The tools a turn is built out of: look, start_stint, travel_to and wait."""

from __future__ import annotations

from typing import Mapping, Sequence

from pydantic_ai.toolsets import FunctionToolset

from ... import walk
from ...briefs import Brief, TravelState
from ..describe import describe_world
from ..toolset import PlannerDeps
from ..validation import (
    refuse_dead_targets,
    validated_hails,
    validated_places,
    validated_shouts,
)
from pydantic_ai import RunContext

# The one place name the code-driven `travel_to` walk uses.
DESTINATION_PLACE = "destination"

# The one place name the code-driven `travel_to` walk uses.
DESTINATION_PLACE = "destination"

# `travel_to`'s own stint end reasons. Code decides both: Jev's `done` score
# took several ticks to agree that a finished walk was finished, and 30 of 89
# walks in the 2026-09-19 run ended `ticks_exhausted` instead. The walk itself
# — the budget, the arrival test and the wording — lives in `walk.py`, which
# every other walking caller uses too.
TRAVEL_ARRIVED = walk.ARRIVED
TRAVEL_ARRIVED_NEXT_TO = walk.ARRIVED_NEXT_TO
TRAVEL_TICKS_PER_STEP = walk.TICKS_PER_STEP
TRAVEL_TICK_ALLOWANCE = walk.TICK_ALLOWANCE
TRAVEL_MIN_TICKS = walk.MIN_TICKS
TRAVEL_MAX_TICKS = walk.MAX_TICKS

travel_budget = walk.tick_budget
travel_arrival = walk.arrival
travel_arrival_text = walk.arrival_text


def register_head(tools: FunctionToolset[PlannerDeps]) -> None:
    """look, start_stint and travel_to: the spine of a turn."""

    @tools.tool
    async def look(ctx: RunContext[PlannerDeps]) -> str:
        """Look around: your stats, inventory, known objects, and recent events."""
        return describe_world(ctx.deps.bridge.model)

    @tools.tool
    async def start_stint(
        ctx: RunContext[PlannerDeps],
        instruction: str,
        success_condition: str,
        max_ticks: int,
        notes: str = "",
        check_every: int = 1,
        shouts: Sequence[str] = (),
        hails: Sequence[Mapping[str, str]] = (),
        places: Mapping[str, Sequence[int]] = {},
    ) -> str:
        """Hand control to Jev until the brief is done, then read the report.

        Jev cannot speak on its own: the only lines it can say are the exact
        phrases you give it here in `shouts` and `hails`. An instruction like
        "talk to finn about the wall" does nothing by itself; give Jev a hail
        for finn.

        Args:
            instruction: what Jev should do, concretely, in one or two sentences.
            success_condition: what Jev should be able to see when it is done.
            max_ticks: hard tick budget; the stint ends when it runs out.
            notes: extra hints, for example "eat a berry when food is below 40".
            check_every: ask Jev every Nth tick and repeat the last action between.
            shouts: the exact phrases Jev may shout during this stint, for
                example ["Stone to spare at the rocks.", "Come to the workshop."].
                Good for calling people to Jev's spot or flagging an emergency
                while it works; not good for a back-and-forth, since nobody
                can answer it. Say in the instruction when to use each. Jev
                cannot shout anything else; leave it empty and Jev stays
                quiet.
            hails: settlers Jev may address, each as
                {"settler": "dov", "line": "Dov, can we split the wall work?",
                "purpose": "agree who builds which side"}, at most 3.
                `purpose` is optional: what you want out of the conversation
                the hail starts, shown only to this settler once it is seated.
                Next to that settler Jev says the line and a conversation with
                the two of them starts; further off it can walk over first.
                Good for having Jev start a conversation with a particular
                settler when it meets them, or at the point you name; say in
                the instruction when. Jev cannot invent one, and a settler you
                have not met is refused.
            places: named map positions Jev may walk to, for example
                {"the_lake_shore": [1539, 974]}. Jev is offered one step toward
                each and is shown how far off it is; it never sees the
                coordinate. Names are lowercase letters, digits and
                underscores, at most 6 per brief.
        """
        refuse_dead_targets(
            ctx.deps.bridge.model, instruction, success_condition, notes
        )
        brief = Brief(
            instruction=instruction,
            success_condition=success_condition,
            max_ticks=max(1, max_ticks),
            notes=notes,
            check_every=max(1, check_every),
            shouts=validated_shouts(shouts),
            hails=validated_hails(hails, ctx.deps.bridge.model),
            places=validated_places(places, ctx.deps.bridge.model),
        )
        report = await ctx.deps.bridge.run_stint(brief)
        return report.to_text()

    @tools.tool
    async def travel_to(ctx: RunContext[PlannerDeps], x: int, y: int) -> str:
        """Walk to a map position, however many ticks that takes.

        The walk ends the tick you stand on (x, y). If that tile cannot be
        stood on - water, a wall, a bush, another settler - it ends when you
        are next to it, which is as close as walking gets. If you are already
        there when you call this, it returns at once and costs no tick. It
        also ends early if there is no way through, if a wolf is on you or if
        you get hungry; the result says which.

        Args:
            x: destination map x.
            y: destination map y.
        """
        model = ctx.deps.bridge.model
        target = (x, y)
        arrival = travel_arrival(model, target)
        if arrival:
            where = travel_arrival_text(arrival, model.position, target)
            return f"no walk needed: {where}. No tick spent."
        brief = Brief(
            instruction="Walk to the destination.",
            success_condition="you are standing on the destination",
            max_ticks=travel_budget(model, target),
            notes="Follow the path; react to danger.",
            travel=TravelState(target=target, label=DESTINATION_PLACE),
            # Jev reasons about offsets, never coordinates, so the target is a
            # named place and the numbers stay on this side of the call.
            places={DESTINATION_PLACE: target},
        )
        report = await ctx.deps.bridge.run_stint(
            brief, end_check=lambda current: travel_arrival(current, target)
        )
        text = report.to_text()
        if report.end_reason in (TRAVEL_ARRIVED, TRAVEL_ARRIVED_NEXT_TO):
            where = travel_arrival_text(report.end_reason, report.end_position, target)
            return f"{text}\n{where}"
        return text


def register_wait(tools: FunctionToolset[PlannerDeps]) -> None:
    """wait: do nothing for a few ticks."""

    @tools.tool
    async def wait(ctx: RunContext[PlannerDeps], ticks: int = 1) -> str:
        """Do nothing for a few ticks."""
        return await ctx.deps.bridge.wait_ticks(max(1, ticks))
