"""Options that put the actor at a table: joining a conversation, and hailing."""

from __future__ import annotations

from typing import Sequence

from ..actions import hail_attempt, hail_refusal, join_attempt
from ..briefs import MAX_BRIEF_HAILS, BriefHail, Option, TravelState
from ..geometry import Coord, chebyshev
from ..walk import plan_step
from ..worldmodel import WorldModel
from .common import (
    HAIL_KEY_PREFIX,
    JOIN_CONVERSATION_KEY_PREFIX,
    JOIN_CONVERSATION_OPTION_LIMIT,
    _move,
)


def _conversation_options(
    model: WorldModel, position: Coord, travel: TravelState | None
) -> list[Option]:
    """Joining a conversation in view that still has a free seat.

    Next to the anchor the option is the join intent itself; further away it is
    a code-owned walk to the anchor, exactly like the heard-shout option.
    """
    if model.my_conversation() is not None:
        return []
    options: list[Option] = []
    for conversation in model.conversations():
        if len(options) >= JOIN_CONVERSATION_OPTION_LIMIT:
            break
        if conversation.free_seats <= 0:
            continue
        distance = chebyshev(conversation.anchor, position)
        seated = ", ".join(conversation.participants) or "nobody"
        key = f"{JOIN_CONVERSATION_KEY_PREFIX}{conversation.conversation_id}"
        if distance == 1:
            options.append(
                Option(
                    key=key,
                    description=(
                        f"join the conversation {conversation.conversation_id} "
                        f"with {seated}; in it you speak when your turn comes "
                        f"round, and {conversation.free_seats} seats are free"
                    ),
                    intent=join_attempt(model, conversation.conversation_id).intent,
                    clears_travel=True,
                )
            )
            continue
        if distance == 0:
            # Standing on the anchor is not a seat; a move option gets off it.
            continue
        step = plan_step(model, position, conversation.anchor, stop_adjacent=True)
        if not step.found:
            continue
        options.append(
            Option(
                key=key,
                description=(
                    f"walk to the conversation {conversation.conversation_id} "
                    f"with {seated}, {distance} tiles away, and take one of its "
                    f"{conversation.free_seats} free seats"
                ),
                intent=_move(step.direction),
                travel_target=TravelState(
                    target=conversation.anchor,
                    label=f"the conversation {conversation.conversation_id}",
                    stop_adjacent=True,
                ),
            )
        )
    return options


def _hail_options(
    model: WorldModel,
    position: Coord,
    hails: Sequence[BriefHail],
) -> list[Option]:
    """Addressing a settler the brief named, or walking over to do it.

    Not offered while this actor holds a seat, nor for a settler that is dead,
    asleep or already in a conversation: the world would refuse every one of
    those, and Jev would spend the stint being told no.
    """
    options: list[Option] = []
    for hail in hails[:MAX_BRIEF_HAILS]:
        if hail_refusal(model, hail.settler):
            continue
        target = model.entities[hail.settler]
        key = f"{HAIL_KEY_PREFIX}{hail.settler}"
        distance = chebyshev(target.position, position)
        if distance == 1:
            options.append(
                Option(
                    key=key,
                    description=(
                        f'say "{hail.line}" to {hail.settler}, next to you: a '
                        "conversation with the two of you starts and "
                        f"{hail.settler} answers first"
                    ),
                    intent=hail_attempt(model, hail.settler, hail.line).intent,
                    clears_travel=True,
                )
            )
            continue
        step = plan_step(model, position, target.position, stop_adjacent=True)
        if not step.found:
            continue
        options.append(
            Option(
                key=key,
                description=(
                    f"walk to {hail.settler}, {distance} tiles away, to say "
                    f'"{hail.line}" and start a conversation with them'
                ),
                intent=_move(step.direction),
                travel_target=TravelState(
                    target=target.position,
                    label=f"{hail.settler}, to say your line to them",
                    stop_adjacent=True,
                ),
            )
        )
    return options
