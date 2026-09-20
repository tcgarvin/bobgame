"""Preconditions and intent construction both layers share.

An action the settler can take exists twice over: as one of the options Jev
picks from, and as a planner tool. When the two disagree about whether the
action is legal right now, the settler pays for it — a planner `sleep` at
fatigue 3 spent a tick on a refusal Jev's option layer would never have
offered, and a planner `shout` ignored the cooldown Jev obeys.

An `Attempt` is the one answer: the intent to submit, or the sentence saying
why not. `options.py` turns an allowed one into an `Option`; a planner tool
returns the refusal text or hands the intent to `bridge.direct_action`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import world_pb2 as pb
from . import items
from .worldmodel import WorldModel

# Shouting. The cooldown keeps twelve settlers from filling every ear; it is
# physics, so it binds Jev and the planner alike. `MAX_SHOUT_LENGTH` is the
# one truncation rule for a shout, whoever writes it.
SHOUT_COOLDOWN_TICKS = 8
MAX_SHOUT_LENGTH = 120


def _no_intent() -> pb.Intent:
    """The empty intent a refused `Attempt` carries.

    Protobuf has no null, and an empty message reads better than an optional
    nobody may dereference. It is built per attempt because a proto message is
    mutable and a shared one would be a shared mutable default.
    """
    return pb.Intent()


@dataclass(frozen=True)
class Attempt:
    """Whether an action may be tried right now, and what to say when not.

    Exactly one of the two halves is meaningful: a refused attempt carries
    an empty intent, and an allowed one carries an empty `refusal`.
    """

    intent: pb.Intent = field(default_factory=_no_intent)
    refusal: str = ""
    # What the actor is doing, as `direct_action` and the trace should read it.
    description: str = ""

    @property
    def allowed(self) -> bool:
        """Whether there is an intent to submit."""
        return not self.refusal

    @classmethod
    def refused(cls, reason: str) -> "Attempt":
        """An attempt that costs no tick, carrying the reason it costs none."""
        return cls(refusal=reason)


# --------------------------------------------------------------------------
# Speaking
# --------------------------------------------------------------------------


def shout_attempt(model: WorldModel, text: str) -> Attempt:
    """Shout `text`, unless the last shout was too recent.

    The cooldown is the world's pace, not a rule of etiquette: a settler who
    shouts every tick drowns out the sixty tiles around it.
    """
    last = model.last_own_shout_tick()
    if last >= 0 and model.tick - last < SHOUT_COOLDOWN_TICKS:
        waited = model.tick - last
        return Attempt.refused(
            f"you shouted {waited} tick(s) ago; a shout carries every "
            f"{SHOUT_COOLDOWN_TICKS} ticks. No tick spent."
        )
    line = text[:MAX_SHOUT_LENGTH]
    return Attempt(
        intent=pb.Intent(say=pb.SayIntent(text=line, channel=items.SHOUT_CHANNEL)),
        description=f"shout {line[:60]!r}",
    )


def converse_intent(
    action: str,
    *,
    conversation_id: str = "",
    text: str = "",
    direction: pb.Direction = pb.DIRECTION_UNSPECIFIED,
    target_entity_id: str = "",
) -> pb.Intent:
    """A `ConverseIntent` wrapped in an Intent, with the text truncated.

    `target_entity_id` is the settler a `hail` addresses. Every converse intent
    in the package is built here, so the truncation happens exactly once.
    """
    return pb.Intent(
        converse=pb.ConverseIntent(
            action=action,
            conversation_id=conversation_id,
            text=text[: items.CONVERSATION_TEXT_LIMIT],
            direction=direction,
            target_entity_id=target_entity_id,
        )
    )


def give_intent(entity_id: str, kind: str, amount: int) -> pb.Intent:
    """A `GiveIntent` wrapped in an Intent."""
    return pb.Intent(
        give=pb.GiveIntent(target_entity_id=entity_id, kind=kind, amount=max(1, amount))
    )


# --------------------------------------------------------------------------
# Sleeping
# --------------------------------------------------------------------------


def sleep_attempt(model: WorldModel, bed: str = "") -> Attempt:
    """Lie down on `bed` (or the ground), unless the body is not tired enough.

    The world refuses a sleep below `items.MIN_SLEEP_FATIGUE`, so trying it
    only burns a tick; the option layer has never offered one and the planner's
    `sleep` now says the same thing instead of finding out.
    """
    info = model.self_info
    if info.fatigue < items.MIN_SLEEP_FATIGUE:
        return Attempt.refused(
            f"you are not tired enough to sleep: fatigue {info.fatigue}, and "
            f"the world refuses a sleep below {items.MIN_SLEEP_FATIGUE}. "
            "No tick spent."
        )
    return Attempt(
        intent=pb.Intent(sleep=pb.SleepIntent(object_id=bed)),
        description=f"sleep on {bed or 'the ground'}",
    )
