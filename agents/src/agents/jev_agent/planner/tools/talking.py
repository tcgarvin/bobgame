"""The spoken channels: conversations, the walks into them, and shouting."""

from __future__ import annotations

from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from ... import items
from ... import actions
from ...actions import converse_intent, hail_attempt, hail_refusal, shout_attempt
from ...briefs import Brief
from ...conversation import ACTION_JOIN, ACTION_OPEN, free_seat_tiles
from ...geometry import Coord, chebyshev
from ...outcomes import ActionOutcome
from ...walk import WalkDriver
from ..toolset import PlannerDeps
from ..validation import direction_value


def speak_result(outcome: ActionOutcome, verb: str, radius: int) -> str:
    """Name who heard a say, in place of the world's raw hearer list.

    Anything that is not a successful say (a failure, an `interrupted: ...`,
    an asleep rejection) is rendered as it stands.
    """
    if not outcome.ok or outcome.action != actions.SAY_ACTION_TYPE:
        return outcome.text()
    heard = outcome.heard
    if not heard:
        return f"nobody was within {radius} tiles to hear it"
    return f"{verb} to {', '.join(heard)} (within {radius} tiles)"


async def sit_through(ctx: RunContext[PlannerDeps], outcome: ActionOutcome) -> str:
    """Wait out the conversation the last action started, then report it."""
    if not outcome.ok:
        return outcome.text()
    report = await ctx.deps.bridge.await_conversation()
    if report is None:
        return f"{outcome.text()}\nno conversation started"
    return f"{outcome.text()}\n{report.to_text()}"


# How many times the code-owned walk to a moving settler re-aims before it
# gives up; every leg spends part of the one tick budget.
WALK_LEGS = 3

# `EntityInfo.entity_type` for a wolf, which cannot be hailed.
WOLF_ENTITY_TYPE = "wolf"


async def _hail(
    ctx: RunContext[PlannerDeps], entity_id: str, opening_line: str, max_ticks: int
) -> str:
    """Walk up to a settler and address it, starting a conversation (docs/09, section 9)."""
    bridge = ctx.deps.bridge
    line = opening_line.strip()
    if not line:
        return f"a hail needs an opening line: what you say when you reach {entity_id}"
    refusal = hail_refusal(bridge.model, entity_id)
    if refusal:
        return refusal

    walked = await _walk_to_settler(ctx, entity_id, max_ticks)
    known = bridge.model.entities.get(entity_id)
    head = f"{walked}\n" if walked else ""
    if known is None:
        return f"{head}you have lost track of {entity_id}"
    if known.asleep and known.last_seen == bridge.model.tick:
        return f"{head}{entity_id} is asleep now that you have reached them"
    attempt = hail_attempt(bridge.model, entity_id, line)
    if not attempt.allowed:
        return f"{head}{attempt.refusal}"
    outcome = await bridge.direct_action(attempt.intent, attempt.description)
    seated = await sit_through(ctx, outcome)
    if not outcome.ok:
        return f"{head}{seated}; {entity_id} was last seen at {known.position}"
    return f"{head}{seated}"


async def _walk_to_settler(
    ctx: RunContext[PlannerDeps], entity_id: str, max_ticks: int
) -> str:
    """Walk onto a free tile next to a settler, re-aiming as it moves.

    The target has its own plans, so each leg walks to where it was last seen
    and the next leg aims again. The whole walk shares one tick budget.
    """
    bridge = ctx.deps.bridge
    legs: list[str] = []
    ticks_left = max(1, max_ticks)
    for _ in range(WALK_LEGS):
        known = bridge.model.entities.get(entity_id)
        if known is None:
            legs.append(f"you have lost track of {entity_id}")
            break
        if chebyshev(bridge.model.position, known.position) == 1:
            break
        tiles = free_seat_tiles(bridge.model, known.position)
        if not tiles:
            legs.append(f"every tile next to {entity_id} is taken or blocked")
            break
        driver = WalkDriver(tiles, entity_id)
        brief = Brief(
            instruction=f"Walk to a free tile next to {entity_id}.",
            success_condition=f"you are standing next to {entity_id}",
            max_ticks=ticks_left,
        )
        report = await bridge.run_stint(brief, driver)
        legs.append(report.to_text())
        ticks_left -= report.ticks_used
        if ticks_left <= 0 or report.end_reason != WalkDriver.ARRIVED:
            # Out of budget, no path, or the walk ended for a reason of its
            # own (a reflex, or a conversation that started on the way).
            break
    return "\n".join(legs)


async def _walk_to_anchor(
    ctx: RunContext[PlannerDeps], conversation_id: str, max_ticks: int
) -> str:
    """Run the code-owned walk to a free tile beside the conversation's anchor."""
    bridge = ctx.deps.bridge
    conversation = bridge.model.conversation_by_id(conversation_id)
    if conversation is None:
        return f"{conversation_id} is gone"
    return await _walk_next_to(ctx, conversation.anchor, conversation_id, max_ticks)


async def _walk_next_to(
    ctx: RunContext[PlannerDeps], target: Coord, label: str, max_ticks: int
) -> str:
    """Run the code-owned walk onto a free tile next to `target`.

    Shared by `join_conversation`, whose target is the anchor, and `talk_to`,
    whose target is the settler who is open to talk.
    """
    bridge = ctx.deps.bridge
    tiles = free_seat_tiles(bridge.model, target)
    if not tiles:
        return f"every tile next to {label} is taken or blocked"
    driver = WalkDriver(tiles, f"a free tile next to {label}")
    brief = Brief(
        instruction=f"Walk to a free tile next to {label}.",
        success_condition=f"you are standing next to {target}",
        max_ticks=max(1, max_ticks),
    )
    report = await bridge.run_stint(brief, driver)
    return report.to_text()


def register_shout(tools: FunctionToolset[PlannerDeps]) -> None:
    """shout: one line to everyone within sixty tiles, with no reply."""

    @tools.tool
    async def shout(ctx: RunContext[PlannerDeps], text: str) -> str:
        """Shout; every settler within sixty tiles hears it and where it came from.

        Good for emergencies and for calling people to a spot. It is one line
        with no reply possible, so it is not good for working anything out.
        Say where and why: "Wolf at (1540, 970), two of us fighting, come!".
        """
        attempt = shout_attempt(ctx.deps.bridge.model, text)
        if not attempt.allowed:
            return attempt.refusal
        outcome = await ctx.deps.bridge.direct_action(
            attempt.intent, attempt.description
        )
        return speak_result(outcome, "shouted", items.SHOUT_RADIUS)


def register_conversations(tools: FunctionToolset[PlannerDeps]) -> None:
    """open_conversation, join_conversation and talk_to."""

    @tools.tool
    async def open_conversation(
        ctx: RunContext[PlannerDeps], direction: str, opening_line: str, purpose: str
    ) -> str:
        """Open a conversation next to you and stay in it until it is over.

        Good for gathering people around a spot for back-and-forth: the only
        channel where the others can answer you.

        The anchor is the neighbouring tile in `direction`; it must be free.
        The opening line is said out loud, with the conversation attached to
        it, so everyone within ten tiles hears it and can walk over and join.
        This call returns when the conversation has ended, with the transcript,
        what changed hands and the note you kept.

        Args:
            direction: N, NE, E, SE, S, SW, W or NW: where the anchor tile goes.
            opening_line: what you say as you open it, at most 300 characters.
            purpose: what you want out of this conversation; only you see it,
                and it is handed back to you while you are in the conversation.
        """
        value = direction_value(direction)
        ctx.deps.bridge.set_conversation_purpose(purpose)
        try:
            outcome = await ctx.deps.bridge.direct_action(
                converse_intent(ACTION_OPEN, text=opening_line, direction=value),
                f"open a conversation to the {direction.strip().upper()}",
            )
            return await sit_through(ctx, outcome)  # noqa: RET504
        finally:
            ctx.deps.bridge.set_conversation_purpose("")

    @tools.tool
    async def join_conversation(
        ctx: RunContext[PlannerDeps], conversation_id: str, max_ticks: int = 40
    ) -> str:
        """Walk to a conversation you can see, take a seat, and talk until it ends.

        Good for joining in on a conversation someone else already opened,
        rather than starting your own with `talk_to` or `open_conversation`.

        Code does the walking: it picks a free tile next to the anchor and goes
        there. The call returns when the conversation has ended.

        Args:
            conversation_id: the id `look` printed for it.
            max_ticks: tick budget for the walk there.
        """
        bridge = ctx.deps.bridge
        conversation = bridge.model.conversation_by_id(conversation_id)
        if conversation is None:
            known = bridge.model.conversations()
            if known:
                listing = ", ".join(f"{c.conversation_id} at {c.anchor}" for c in known)
                return (
                    f"you have not seen a conversation called {conversation_id!r}; "
                    f"in view: {listing}. talk_to walks up to a settler and "
                    "starts one instead of joining an existing anchor"
                )
            return (
                f"you have not seen a conversation called {conversation_id!r} "
                "and none is in view; talk_to walks up to a settler and starts "
                "one"
            )
        if conversation.free_seats <= 0:
            return (
                f"{conversation_id} has no free seat "
                f"({len(conversation.participants)} settlers in it)"
            )
        walked = ""
        if chebyshev(bridge.model.position, conversation.anchor) != 1:
            walked = await _walk_to_anchor(ctx, conversation_id, max_ticks)
            if chebyshev(bridge.model.position, conversation.anchor) != 1:
                return f"{walked}\nyou are not next to {conversation_id} yet"
        outcome = await bridge.direct_action(
            converse_intent(ACTION_JOIN, conversation_id=conversation_id),
            f"join {conversation_id}",
        )
        seated = await sit_through(ctx, outcome)
        return f"{walked}\n{seated}" if walked else seated

    @tools.tool
    async def talk_to(
        ctx: RunContext[PlannerDeps],
        entity_id: str,
        opening_line: str,
        purpose: str,
        max_ticks: int = 40,
    ) -> str:
        """Walk up to a settler, start a conversation, and stay until it is over.

        Good for reaching one particular settler for back-and-forth, wherever
        they are, rather than waiting for them to come to you or to a board.

        Code walks you to a free tile next to them and hails them: you say
        `opening_line` out loud, a conversation appears on a free tile beside
        you both holding the two of you, your line is its first line and they
        speak next. They do not have to be expecting you, but a settler cannot
        be hailed until 60 ticks after its last conversation ended, and
        neither of you can already be in one, be asleep or be dead. A sleeping
        settler cannot be hailed at all; this refuses at once if you can see
        them asleep, and says so if they turn out to be asleep once you reach
        them. The call returns when the conversation has ended.

        Args:
            entity_id: the settler you want to talk to.
            opening_line: what you say to them as you walk up, at most 300
                characters.
            purpose: what you want out of this conversation; only you see it,
                and it is handed back to you while you are in the conversation.
            max_ticks: tick budget for the walk there.
        """
        ctx.deps.bridge.set_conversation_purpose(purpose)
        try:
            return await _hail(ctx, entity_id, opening_line, max_ticks)
        finally:
            ctx.deps.bridge.set_conversation_purpose("")
