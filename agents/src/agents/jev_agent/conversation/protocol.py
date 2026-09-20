"""The vocabulary of conversation mode: action names, end reasons, call shapes.

Nothing here talks to the world or to a model; it is the words the session, the
converser and the report all have to agree on.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from pydantic import BaseModel, Field

from .. import items

ACTION_OPEN = "open"
ACTION_JOIN = "join"
# Mirrored in `items.py` with the other world constants, and named here beside
# the actions it belongs with.
ACTION_HAIL = items.ACTION_HAIL
# The world's own word for the target's side of a hail: a seat it never asked
# for (docs/09 section 9).
ACTION_HAILED = items.ACTION_HAILED
ACTION_SPEAK = "speak"
ACTION_PASS = "pass"
ACTION_LEAVE = "leave"
ACTION_GIVE = "give"
ACTION_EAT = "eat"

CONVERSE_ACTION_TYPE = items.CONVERSE_ACTION_TYPE
GIVE_ACTION_TYPE = "give"
EAT_ACTION_TYPE = "eat"

# The action types the world reports back for a converser move.
MOVE_ACTION_TYPES = (CONVERSE_ACTION_TYPE, GIVE_ACTION_TYPE, EAT_ACTION_TYPE)

# End reasons reported to the planner.
END_CLOSED = "closed"
END_LEFT = "left"
END_REMOVED = "removed"
END_DIED = "died"
END_NOBODY_JOINED = "nobody joined"
# The actor fell asleep with a seat: the body is gone from the conversation
# until it wakes, so the session ends here. On a new-moon night the world puts
# everyone to sleep and closes every conversation on the same tick
# (docs/14 section 1), which is the reason the planner is given.
END_ASLEEP = "asleep"
END_NEW_MOON = "new_moon"

# What each of those two reasons means, added to the report so the planner is
# told the physics rather than left with a word.
SLEEP_END_TEXT: Mapping[str, str] = {
    END_ASLEEP: "You fell asleep, so your seat ended.",
    END_NEW_MOON: (
        "The new moon put everyone to sleep and closed every conversation on "
        "the island on the same tick."
    ),
}

# How the actor came to hold its seat, as the `conversation_start` trace says
# it. The hailed settler's `via`; the hailer's is `hail`, the world's own word.
VIA_HAILED = ACTION_HAILED
JOIN_ACTIONS = (ACTION_OPEN, ACTION_JOIN, ACTION_HAIL, ACTION_HAILED)
# The seats the actor never asked for: it was hailed.
UNASKED_VIA = (VIA_HAILED,)

# The world creates the conversation object on the tick it accepts the `open`
# or `join`, but an observation can lag by a tick; wait this long for the
# object to show up before declaring the conversation over.
OBJECT_GRACE_TICKS = 3

# The converser's answer must arrive well inside a tick to be worth using.
CONVERSER_TIMEOUT_SECONDS = 60.0

MAX_NOTE_CHARACTERS = 400

# Trace note for an answer the world had already moved past when it arrived.
NOTE_STALE = "stale"

# What is recorded when the world never acted on a move that was submitted.
OUTCOME_NOT_CARRIED_OUT = "the world did not carry it out"

# A move the code refused before it reached the world.
OUTCOME_NO_TARGET = "refused: a give needs the name of who receives it"
OUTCOME_NO_KIND = "refused: a give needs the name of an item in your pack"

# Two sources stamp the same spoken line with different ticks: the speaker's
# own tick in object state, and the tick the listener observed it. Lines this
# close together from the same speaker with the same text are one line.
DUPLICATE_TICK_WINDOW = 2

# Stands in for the answer a cancelled or failed call never produced.
NO_MOVE_ACTION = ""


class ClosingNote(BaseModel):
    """The two things worth keeping once a conversation ends."""

    agreed_or_learned: str = Field(
        default="", description="what was agreed or learned, or empty"
    )
    you_said_you_would: str = Field(
        default="", description="what you yourself said you would do, or empty"
    )


class ConverserMove(BaseModel):
    """One move in a conversation: what to do on this turn."""

    action: str = Field(description="speak, pass, leave, give or eat")
    text: str = Field(default="", description="what to say when the action is speak")
    give_to: str = Field(default="", description="who receives, when giving")
    kind: str = Field(default="", description="item kind to give or eat")
    amount: int = Field(default=1, description="how many to give")


@dataclass(frozen=True)
class MoveCall:
    """One converser move call: its answer and what the call cost.

    `usage` is the block described in docs/11_cost_accounting.md, or empty for
    a converser that makes no model call (a test double, say).
    """

    move: ConverserMove
    usage: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NoteCall:
    """The closing note (docs/09 section 10, item 5) and what it cost.

    `agreed` is what was agreed or learned; `commitment` is what this settler
    itself said it would do. Either may be empty.
    """

    agreed: str = ""
    commitment: str = ""
    usage: Mapping[str, Any] = field(default_factory=dict)


class Converser(Protocol):
    """The language-model half of conversation mode."""

    async def move(self, prompt: str) -> MoveCall:
        """Choose this turn's move."""

    async def note(self, prompt: str) -> NoteCall:
        """What, if anything, to keep from the conversation."""


@dataclass(frozen=True)
class MoveOutcome:
    """What the world made of one move this actor submitted."""

    tick: int
    turn: int
    move: str
    outcome: str

    def as_line(self) -> str:
        """`t272 give 3 berry to cleo -> not enough berry to give`."""
        return f"t{self.tick} {self.move} -> {self.outcome}"


@dataclass(frozen=True)
class CallResult:
    """One finished converser call: its answer and how long it really took."""

    move: ConverserMove
    latency_ms: int
    error: str = ""
    usage: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class PendingCall:
    """The background converser call for one turn."""

    task: asyncio.Task[CallResult]
    turn: int
    started: float

    def elapsed_ms(self) -> int:
        """Milliseconds since the call was started."""
        return int((time.monotonic() - self.started) * 1000)
