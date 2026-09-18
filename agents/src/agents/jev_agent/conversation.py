"""Conversation mode: the actor talks, one world turn at a time.

A conversation is a world object with a seat for up to four settlers and a
round-robin turn order (docs/09_conversation_and_reflex.md section 2). While
the actor holds a seat, this module owns its body: it submits `wait` every tick
except on its own turn, and on its turn it asks the *converser*, a small
language-model agent with no tools, for one move.

The model call runs as a background task, so the tick loop never waits on it
and never misses the intent deadline. Every tick, whether or not it is this
actor's turn, a finished call is collected and an outdated one is thrown away
(cancelled while it is still running), so latency is recorded when the answer
actually arrived rather than when the turn next came round.

The session also keeps the world's own verdict on every move it made this turn,
so the converser is told what its last `give` or `eat` did.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

import structlog
from pydantic import BaseModel, Field
from pydantic_ai import Agent

from .. import world_pb2 as pb
from . import items
from .geometry import (
    ORDERED_DIRECTIONS,
    Coord,
    chebyshev,
    direction_between,
    direction_name,
    offset,
)
from .llm import planner_model_settings, resolve_model_name
from .stint import DriverChoice
from .options import Option
from .pathfinding import find_path
from .tracelog import AgentTrace
from .worldmodel import ConversationInfo, TickDigest, TranscriptLine, WorldModel

logger = structlog.get_logger(__name__)

ACTION_OPEN = "open"
ACTION_JOIN = "join"
ACTION_SPEAK = "speak"
ACTION_PASS = "pass"
ACTION_LEAVE = "leave"
ACTION_GIVE = "give"
ACTION_EAT = "eat"

CONVERSE_ACTION_TYPE = "converse"
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

# `gave 3 stone to mira`, the world's success details for a `give`.
_GAVE_PATTERN = re.compile(r"^gave (?P<amount>\d+) (?P<kind>\S+) to (?P<target>\S+)$")


CONVERSER_NARRATIVE = f"""\
You are one of twelve people who woke up together on a large, wild island with
nothing but your hands. The others are real agents like you. Together, build a
civilization. You are in a
conversation with some of them right now, and this is your turn to act in it.

How a conversation works:
- A conversation sits on an anchor tile. Up to {items.CONVERSATION_MAX_PARTICIPANTS} settlers stand on tiles next to
  it and take turns in the order they joined. Anyone within {items.SAY_RADIUS} tiles hears what
  is said, whether or not they are in it.
- While you are in the conversation your body stays on its tile and the moves
  below are the only things you can do. Walking, gathering, crafting, placing
  and fighting only happen after you `leave` or the conversation closes.
  The world keeps ticking while you talk: hunger drops 1 every 4 ticks and at
  hunger 0 you lose health.
- On your turn you may `speak` (one line of at most {items.CONVERSATION_TEXT_LIMIT} characters), `pass`,
  `leave`, `give` items to someone in the conversation or standing next to
  you, or `eat` one item from your pack (a berry restores 20 hunger). Speaking
  or passing ends your turn; giving and eating do not, so afterwards you are
  asked again on the next tick.
- A turn you do not use within {items.CONVERSATION_TURN_TICKS} ticks counts as a pass. The conversation
  closes when fewer than two settlers are left in it, when everyone passes in
  one full round, or after {items.CONVERSATION_MAX_UTTERANCES} lines.
- A `give` needs the item in your pack, the exact item name, and an amount.

Answer with one move: the action, the text if you are speaking, the target,
item kind and amount if you are giving, and the item kind if you are eating.
"""

NOTE_INSTRUCTION = (
    "The conversation is over. Write one line to keep in your notes, or an "
    "empty string to keep nothing. Only the line itself, no preamble."
)


class ConverserMove(BaseModel):
    """One move in a conversation: what to do on this turn."""

    action: str = Field(description="speak, pass, leave, give or eat")
    text: str = Field(default="", description="what to say when the action is speak")
    give_to: str = Field(default="", description="who receives, when giving")
    kind: str = Field(default="", description="item kind to give or eat")
    amount: int = Field(default=1, description="how many to give")


class Converser(Protocol):
    """The language-model half of conversation mode."""

    async def move(self, prompt: str) -> ConverserMove:
        """Choose this turn's move."""

    async def note(self, prompt: str) -> str:
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


@dataclass
class PendingCall:
    """The background converser call for one turn."""

    task: asyncio.Task[CallResult]
    turn: int
    started: float

    def elapsed_ms(self) -> int:
        """Milliseconds since the call was started."""
        return int((time.monotonic() - self.started) * 1000)


async def run_converser(
    converser: Converser, prompt: str, timeout: float
) -> CallResult:
    """Ask the converser for a move and time the call from inside.

    Timing here rather than at collection time is the point: the tick loop may
    only look at the task several ticks later, so a latency measured then would
    be the wait, not the call. A failed call becomes a `pass` with the error
    recorded, because one bad answer must not end the conversation.
    """
    started = time.monotonic()
    try:
        move = await asyncio.wait_for(converser.move(prompt), timeout)
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001 - a failed converser is not fatal
        elapsed = int((time.monotonic() - started) * 1000)
        logger.warning("converser_call_failed", error=str(error))
        return CallResult(ConverserMove(action=ACTION_PASS), elapsed, str(error))
    return CallResult(move, int((time.monotonic() - started) * 1000))


class ModelConverser:
    """A `Converser` backed by two pydantic-ai agents on the planner's model.

    One has the structured `ConverserMove` output and no tools; the other
    writes the note kept after the conversation. Neither has any tool and
    neither can touch the world.
    """

    def __init__(self, model_name: str = "") -> None:
        self.model_name = resolve_model_name(model_name)
        settings = planner_model_settings(self.model_name)
        self.move_agent: Agent[None, ConverserMove] = Agent(
            self.model_name,
            output_type=ConverserMove,
            system_prompt=CONVERSER_NARRATIVE,
            model_settings=settings,
            retries=2,
        )
        self.note_agent: Agent[None, str] = Agent(
            self.model_name,
            output_type=str,
            system_prompt=CONVERSER_NARRATIVE,
            model_settings=settings,
            retries=1,
        )

    async def move(self, prompt: str) -> ConverserMove:
        """Ask the model for one move."""
        result = await self.move_agent.run(prompt)
        return result.output

    async def note(self, prompt: str) -> str:
        """Ask the model what to keep from the conversation."""
        result = await self.note_agent.run(prompt)
        return result.output.strip()


@dataclass
class ConversationReport:
    """What the planner reads once the conversation has ended."""

    conversation_id: str
    start_tick: int
    end_tick: int
    participants: tuple[str, ...]
    end_reason: str
    transcript: tuple[TranscriptLine, ...]
    given: tuple[str, ...] = ()
    received: Mapping[str, int] = field(default_factory=dict)
    note: str = ""

    def to_text(self) -> str:
        """Render the report for the planner's tool result."""
        lines = [
            f"CONVERSATION REPORT: {self.conversation_id}",
            f"  ticks: {self.start_tick}-{self.end_tick}",
            f"  participants: {', '.join(self.participants) or 'nobody else'}",
            f"  ended because: {self.end_reason}",
        ]
        if self.transcript:
            lines.append("  transcript:")
            lines.extend(f"    {line.as_line()}" for line in self.transcript)
        else:
            lines.append("  transcript: nothing was said")
        if self.given:
            lines.append("  you gave: " + "; ".join(self.given))
        if self.received:
            received = ", ".join(
                f"{kind} +{count}" for kind, count in sorted(self.received.items())
            )
            lines.append(f"  items you were given: {received}")
        lines.append(f"  note kept: {self.note or '(none)'}")
        return "\n".join(lines)


def free_seat_tiles(model: WorldModel, anchor: Coord) -> list[Coord]:
    """Walkable, unoccupied tiles next to `anchor`, nearest to the actor first.

    The anchor itself is excluded: the world requires a participant to stand
    beside it, not on it.
    """
    tiles = [
        offset(anchor, direction)
        for direction in ORDERED_DIRECTIONS
        if model.is_walkable(offset(anchor, direction))
    ]
    tiles.sort(key=lambda tile: (chebyshev(tile, model.position), tile))
    return tiles


class ApproachDriver:
    """A `StintDriver` that walks the actor onto one of a set of target tiles.

    Used by the `join_conversation` tool: choosing a free seat next to an
    anchor is arithmetic, so code does the walking and Jev is not involved.
    """

    name = "approach"

    # Stint end reasons this driver produces.
    ARRIVED = "arrived"
    NO_PATH = "no_path"

    def __init__(self, targets: Sequence[Coord], label: str) -> None:
        self.targets = list(targets)
        self.label = label
        self._path: list[Coord] = []

    def stop_reason(self, model: WorldModel) -> str:
        """Arrived, blocked, or `""` to keep walking."""
        if model.position in self.targets:
            return self.ARRIVED
        self._path = self._best_path(model)
        if not self._path:
            return self.NO_PATH
        return ""

    def choose(self, model: WorldModel) -> DriverChoice:
        """Take the next step along the path found by `stop_reason`."""
        direction = direction_between(model.position, self._path[0])
        return DriverChoice(
            option=Option(
                key=f"approach_step:{direction_name(direction)}",
                description=f"walk toward {self.label}",
                intent=pb.Intent(move=pb.MoveIntent(direction=direction)),
            ),
            note=f"{len(self._path)} steps to {self.label}",
        )

    def summary(self) -> str:
        """One line of progress for the stint report."""
        return f"APPROACH {self.label}: {len(self._path)} steps left"

    def _best_path(self, model: WorldModel) -> list[Coord]:
        best: list[Coord] = []
        for target in self.targets:
            path = find_path(model, model.position, target)
            if path and (not best or len(path) < len(best)):
                best = path
        return best


def converse_intent(
    action: str,
    *,
    conversation_id: str = "",
    text: str = "",
    direction: pb.Direction = pb.DIRECTION_UNSPECIFIED,
) -> pb.Intent:
    """A `ConverseIntent` wrapped in an Intent, with the text truncated."""
    return pb.Intent(
        converse=pb.ConverseIntent(
            action=action,
            conversation_id=conversation_id,
            text=text[: items.CONVERSATION_TEXT_LIMIT],
            direction=direction,
        )
    )


def give_intent(entity_id: str, kind: str, amount: int) -> pb.Intent:
    """A `GiveIntent` wrapped in an Intent."""
    return pb.Intent(
        give=pb.GiveIntent(target_entity_id=entity_id, kind=kind, amount=max(1, amount))
    )


def joined_conversation_id(digest: TickDigest) -> str:
    """The conversation the actor joined or opened on this tick, if it did.

    The world reports a successful `ConverseIntent` as an `EntityActed` with
    action type `converse` and details that start with the action name and the
    conversation id, for example `join conv_12`.
    """
    for acted in digest.own_actions:
        if acted.action_type != CONVERSE_ACTION_TYPE or not acted.success:
            continue
        parts = acted.details.split()
        if len(parts) >= 2 and parts[0] in (ACTION_OPEN, ACTION_JOIN):
            return parts[1]
    return ""


def transcript_for(
    model: WorldModel, conversation: ConversationInfo | None, conversation_id: str
) -> tuple[TranscriptLine, ...]:
    """Every line of the conversation this actor can account for, oldest first.

    What the actor heard is the primary source, because the object only keeps
    the last `CONVERSATION_TRANSCRIPT_KEPT` lines. The object's transcript is
    the fallback for lines spoken before the actor joined.
    """
    lines: list[TranscriptLine] = list(model.heard_conversation_lines(conversation_id))
    if conversation is not None:
        lines.extend(conversation.transcript)
    return merge_transcript(lines)


def merge_transcript(lines: Sequence[TranscriptLine]) -> tuple[TranscriptLine, ...]:
    """One entry per spoken line, oldest first, keeping the earliest tick.

    The two sources stamp the same utterance differently: object state carries
    the tick the speaker said it, the heard line the tick this actor observed
    it, usually one later. A line from the same speaker with the same text
    within `DUPLICATE_TICK_WINDOW` ticks of one already kept is that same line.
    """
    kept: list[TranscriptLine] = []
    for line in sorted(lines, key=lambda entry: entry.tick):
        if _already_kept(kept, line):
            continue
        kept.append(line)
    return tuple(kept)


def _already_kept(kept: Sequence[TranscriptLine], line: TranscriptLine) -> bool:
    """Whether `line` repeats one of the recent `kept` lines."""
    for earlier in reversed(kept):
        if line.tick - earlier.tick > DUPLICATE_TICK_WINDOW:
            return False
        if earlier.speaker == line.speaker and earlier.text == line.text:
            return True
    return False


def describe_move(move: ConverserMove) -> str:
    """The move as one short phrase, for the outcome line the converser reads."""
    action = move.action.strip().lower()
    if action == ACTION_GIVE:
        return f"give {move.amount} {move.kind or '?'} to {move.give_to or '?'}"
    if action == ACTION_EAT:
        return f"eat {move.kind or 'berry'}"
    if action == ACTION_SPEAK:
        return "speak"
    return action or "nothing"


class ConversationSession:
    """One conversation, from the tick the actor sits down to the report.

    The tick loop calls `decide` once per tick and checks `finished`; when the
    session has finished, `write_report` makes the one extra model call that
    asks what to keep and appends it to the notes file.
    """

    def __init__(
        self,
        conversation_id: str,
        model: WorldModel,
        converser: Converser,
        *,
        trace: AgentTrace,
        memory_path: Path,
        reflex_line: Callable[[], str] = lambda: "",
        alert_line: Callable[[], str] = lambda: "",
    ) -> None:
        self.conversation_id = conversation_id
        self.model = model
        self.converser = converser
        self.trace = trace
        self.memory_path = memory_path
        self.reflex_line = reflex_line
        self.alert_line = alert_line

        self.start_tick = model.tick
        self.finished = False
        self.end_reason = ""

        self._participants: tuple[str, ...] = ()
        self._seen_object = False
        self._most_participants = 0
        self._leaving = False
        self._given: list[str] = []
        self._received_items: dict[str, int] = {}
        self._prompt = ""
        self._call: PendingCall | None = None
        self._outcomes: list[MoveOutcome] = []
        self._pending_move = ""
        self._pending_turn = -1
        self._pending_tick = 0

    # -- per-tick -----------------------------------------------------------

    def begin(self) -> None:
        """Write the `conversation_start` trace line."""
        self.trace.conversations.write(
            {
                "event": "conversation_start",
                "entity_id": self.model.entity_id,
                "tick": self.model.tick,
                "conversation_id": self.conversation_id,
            }
        )

    def decide(self, digest: TickDigest) -> pb.Intent:
        """This tick's intent; sets `finished` when the seat is gone."""
        self._note_own_outcome(digest)
        self._note_receipts(digest)
        conversation = self.model.conversation_by_id(self.conversation_id)
        # Every tick, not only this actor's turns: an answer the world has
        # already moved past is traced and dropped when it arrives, not when
        # the turn next comes round.
        self._settle_outdated_call(conversation)
        end_reason = self._end_reason(conversation)
        if end_reason:
            self.finish(end_reason)
            return pb.Intent(wait=pb.WaitIntent())
        if conversation is None:
            # The object has not been observed yet; hold the seat and wait.
            return pb.Intent(wait=pb.WaitIntent())

        self._seen_object = True
        self._participants = conversation.participants
        self._most_participants = max(
            self._most_participants, len(conversation.participants)
        )
        if conversation.speaker != self.model.entity_id:
            return pb.Intent(wait=pb.WaitIntent())
        return self._take_turn(conversation)

    def _take_turn(self, conversation: ConversationInfo) -> pb.Intent:
        move = self._collect_move(conversation)
        if move is None:
            self._ask(conversation)
            return pb.Intent(wait=pb.WaitIntent())
        return self._intent_for(move, conversation)

    def _collect_move(self, conversation: ConversationInfo) -> ConverserMove | None:
        """The finished answer for this turn, or None when there is none yet."""
        call = self._call
        if call is None or not call.task.done():
            return None
        self._call = None
        result = call.task.result()
        self._trace_turn(result.move, result.latency_ms, result.error)
        return result.move

    def _settle_outdated_call(self, conversation: ConversationInfo | None) -> None:
        """Trace and drop a call the world has moved past.

        A finished answer is collected and traced as stale at the moment it is
        found; one still running is cancelled, because its answer could only
        ever be stale too. While the conversation object is unobserved nothing
        is dropped: the turn may still be this actor's.
        """
        call = self._call
        if call is None or conversation is None:
            return
        if (
            conversation.speaker == self.model.entity_id
            and conversation.turn_started == call.turn
        ):
            return
        self._call = None
        if call.task.done():
            result = call.task.result()
            self._trace_turn(result.move, result.latency_ms, NOTE_STALE)
            return
        call.task.cancel()
        self._trace_turn(
            ConverserMove(action=NO_MOVE_ACTION),
            call.elapsed_ms(),
            NOTE_STALE,
            cancelled=True,
        )

    def _ask(self, conversation: ConversationInfo) -> None:
        """Start the background model call for this turn, once."""
        if self._call is not None:
            return
        prompt = self.build_prompt(conversation)
        self._prompt = prompt
        self._call = PendingCall(
            task=asyncio.create_task(
                run_converser(self.converser, prompt, CONVERSER_TIMEOUT_SECONDS)
            ),
            turn=conversation.turn_started,
            started=time.monotonic(),
        )

    def _intent_for(
        self, move: ConverserMove, conversation: ConversationInfo
    ) -> pb.Intent:
        """The intent for `move`, or a wait when the code refuses it outright."""
        action = move.action.strip().lower()
        turn = conversation.turn_started
        if action == ACTION_GIVE:
            refusal = self._give_refusal(move)
            if refusal:
                # A give the world is certain to reject is not worth a turn:
                # record the reason and ask again with it in the prompt.
                self._record_outcome(describe_move(move), refusal, turn)
                self._ask(conversation)
                return pb.Intent(wait=pb.WaitIntent())
            self._submit_move(move, turn)
            return give_intent(move.give_to, move.kind, move.amount)
        if action == ACTION_EAT:
            self._submit_move(move, turn)
            return pb.Intent(
                eat=pb.EatIntent(item_type=move.kind.strip() or "berry", amount=1)
            )
        if action == ACTION_LEAVE:
            self._leaving = True
            self._submit_move(move, turn)
            return converse_intent(ACTION_LEAVE, conversation_id=self.conversation_id)
        if action == ACTION_SPEAK and move.text.strip():
            self._submit_move(move, turn)
            return converse_intent(
                ACTION_SPEAK,
                conversation_id=self.conversation_id,
                text=move.text.strip(),
            )
        self._submit_move(ConverserMove(action=ACTION_PASS), turn)
        return converse_intent(ACTION_PASS, conversation_id=self.conversation_id)

    @staticmethod
    def _give_refusal(move: ConverserMove) -> str:
        """Why this `give` cannot be submitted at all, or `""` when it can."""
        if not move.give_to.strip():
            return OUTCOME_NO_TARGET
        if not move.kind.strip():
            return OUTCOME_NO_KIND
        return ""

    # -- move outcomes ------------------------------------------------------

    def _submit_move(self, move: ConverserMove, turn: int) -> None:
        """Remember the move whose outcome the next observation will carry."""
        self._pending_move = describe_move(move)
        self._pending_turn = turn
        self._pending_tick = self.model.tick

    def _note_own_outcome(self, digest: TickDigest) -> None:
        """Record what the world made of the move submitted on the last tick.

        The world answers every entity every tick, so the move submitted last
        tick is either among `own_actions` with its own action type or it never
        reached the world at all.
        """
        pending = self._pending_move
        if not pending:
            return
        self._pending_move = ""
        for acted in digest.own_actions:
            if acted.action_type in MOVE_ACTION_TYPES:
                self._record_outcome(
                    pending, acted.details, self._pending_turn, self._pending_tick
                )
                if acted.action_type == GIVE_ACTION_TYPE and acted.success:
                    self._given.append(acted.details)
                return
        self._record_outcome(
            pending, OUTCOME_NOT_CARRIED_OUT, self._pending_turn, self._pending_tick
        )

    def _record_outcome(
        self, move: str, outcome: str, turn: int, tick: int = 0
    ) -> None:
        """Keep one `move -> outcome` line, verbatim as the world put it.

        `tick` is the tick the move was made; it defaults to the current tick,
        which is when a move refused here and never sent was made.
        """
        self._outcomes.append(
            MoveOutcome(
                tick=tick or self.model.tick,
                turn=turn,
                move=move,
                outcome=outcome or "done",
            )
        )

    def _outcomes_this_turn(self, turn: int) -> list[MoveOutcome]:
        """The outcomes of the moves made since this turn began."""
        return [outcome for outcome in self._outcomes if outcome.turn == turn]

    def _note_receipts(self, digest: TickDigest) -> None:
        """Record items another settler handed to this actor.

        The world shows the receiver the giver's own `EntityActed`, whose
        details read `gave 3 stone to mira` (docs/09 section 3).
        """
        for acted in digest.others_actions:
            if acted.action_type != GIVE_ACTION_TYPE or not acted.success:
                continue
            match = _GAVE_PATTERN.match(acted.details)
            if match is None or match.group("target") != self.model.entity_id:
                continue
            kind = match.group("kind")
            self._received_items[kind] = self._received_items.get(kind, 0) + int(
                match.group("amount")
            )

    def _end_reason(self, conversation: ConversationInfo | None) -> str:
        if not self.model.self_info.alive:
            return END_DIED
        if conversation is None:
            if not self._seen_object:
                if self.model.tick - self.start_tick < OBJECT_GRACE_TICKS:
                    return ""
                return END_NOBODY_JOINED
            if self._most_participants < 2:
                return END_NOBODY_JOINED
            return END_CLOSED
        if not conversation.has(self.model.entity_id):
            return END_LEFT if self._leaving else END_REMOVED
        return ""

    def finish(self, reason: str) -> None:
        """Mark the conversation over for this actor."""
        if self.finished:
            return
        self.finished = True
        self.end_reason = reason
        call = self._call
        self._call = None
        if call is not None and not call.task.done():
            call.task.cancel()

    # -- prompts ------------------------------------------------------------

    def build_prompt(self, conversation: ConversationInfo) -> str:
        """The converser's user message for one turn."""
        info = self.model.self_info
        inventory = (
            ", ".join(
                f"{kind} x{count}" for kind, count in sorted(info.inventory.items())
            )
            or "empty"
        )
        others = [
            name for name in conversation.participants if name != self.model.entity_id
        ]
        parts = [
            f"You are {self.model.entity_id}. It is tick {self.model.tick} and it "
            f"is your turn in conversation {conversation.conversation_id}.",
            f"Seated with you: {', '.join(others) or 'nobody yet'}.",
            f"Your health {info.health}/{info.max_health}, hunger "
            f"{info.hunger}/{info.max_hunger}, wielded "
            f"{info.wielded or 'nothing'}.\nYour pack: {inventory}.",
            "Transcript so far:\n" + self._transcript_text(conversation),
            self._moves_text(conversation.turn_started),
            "Your notes:\n" + read_notes(self.memory_path),
            self.reflex_line(),
        ]
        alert = self.alert_line()
        if alert:
            parts.append(alert)
        parts.append("Make one move.")
        return "\n\n".join(part for part in parts if part)

    def _moves_text(self, turn: int) -> str:
        """What the world made of the moves already made on this turn."""
        outcomes = self._outcomes_this_turn(turn)
        if not outcomes:
            return ""
        body = "\n".join(outcome.as_line() for outcome in outcomes)
        return f"Your moves so far this turn:\n{body}"

    def _transcript_text(self, conversation: ConversationInfo | None) -> str:
        lines = transcript_for(self.model, conversation, self.conversation_id)
        if not lines:
            return "(nothing said yet)"
        return "\n".join(line.as_line() for line in lines)

    def note_prompt(self, transcript: Sequence[TranscriptLine]) -> str:
        """The user message for the one model call made after the conversation."""
        body = "\n".join(line.as_line() for line in transcript) or "(nothing said)"
        return (
            f"You are {self.model.entity_id}. The conversation "
            f"{self.conversation_id} with "
            f"{', '.join(self._other_participants()) or 'nobody'} has ended "
            f"({self.end_reason}).\n\nTranscript:\n{body}\n\n{NOTE_INSTRUCTION}"
        )

    def _other_participants(self) -> list[str]:
        return [name for name in self._participants if name != self.model.entity_id]

    # -- reporting ----------------------------------------------------------

    async def write_report(self) -> ConversationReport:
        """Ask what to keep, append it to the notes, and build the report.

        Called off the tick loop once the session has finished, so the model
        call here cannot delay an intent.
        """
        conversation = self.model.conversation_by_id(self.conversation_id)
        transcript = transcript_for(self.model, conversation, self.conversation_id)
        note = await self._ask_for_note(transcript)
        if note:
            append_conversation_note(
                self.memory_path,
                tick=self.model.tick,
                participants=self._other_participants(),
                text=note,
            )
        report = ConversationReport(
            conversation_id=self.conversation_id,
            start_tick=self.start_tick,
            end_tick=self.model.tick,
            participants=self._participants,
            end_reason=self.end_reason,
            transcript=transcript,
            given=tuple(self._given),
            received=self._received(),
            note=note,
        )
        self.trace.conversations.write(
            {
                "event": "conversation_end",
                "entity_id": self.model.entity_id,
                "tick": self.model.tick,
                "conversation_id": self.conversation_id,
                "end_reason": self.end_reason,
                "participants": list(self._participants),
                "utterances": len(transcript),
                "given": list(self._given),
                "received": self._received(),
                "note": note,
            }
        )
        return report

    async def _ask_for_note(self, transcript: Sequence[TranscriptLine]) -> str:
        try:
            note = await self.converser.note(self.note_prompt(transcript))
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - a failed note is not fatal
            # The conversation is already over; losing the note must not lose
            # the report the planner is waiting for.
            logger.warning("conversation_note_failed", error=str(error))
            return ""
        return note.strip()[:MAX_NOTE_CHARACTERS]

    def _received(self) -> dict[str, int]:
        """What other settlers handed this actor during the conversation."""
        return dict(self._received_items)

    def _trace_turn(
        self,
        move: ConverserMove,
        latency_ms: int,
        note: str,
        *,
        cancelled: bool = False,
    ) -> None:
        payload: dict[str, Any] = {
            "event": "turn",
            "entity_id": self.model.entity_id,
            "tick": self.model.tick,
            "conversation_id": self.conversation_id,
            "prompt": self._prompt,
            "move": move.model_dump(),
            "latency_ms": latency_ms,
        }
        if note:
            payload["note"] = note
        if cancelled:
            payload["cancelled"] = True
        self.trace.conversations.write(payload)

    def status_json(self) -> str:
        """A short status blob for the viewer's agent panel."""
        return json.dumps(
            {
                "conversation_id": self.conversation_id,
                "participants": list(self._participants),
                "started": self.start_tick,
            }
        )


def read_notes(path: Path) -> str:
    """The actor's persistent notes, or a placeholder."""
    if not path.exists():
        return "(no notes yet)"
    return path.read_text(encoding="utf-8").strip() or "(no notes yet)"


def append_conversation_note(
    path: Path, *, tick: int, participants: Sequence[str], text: str
) -> None:
    """Append `- [conversation, tick N, with a, b] <text>` to the notes file."""
    with_whom = ", ".join(participants) or "nobody"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"- [conversation, tick {tick}, with {with_whom}] {text}\n")
