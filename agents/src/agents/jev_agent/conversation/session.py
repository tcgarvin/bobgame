"""One conversation, from the tick the actor sits down to the report.

A conversation is a world object with a seat for up to four settlers and a
round-robin turn order (docs/09_conversation_and_reflex.md section 2). While
the actor holds a seat, this module owns its body: it submits `wait` every tick
except on its own turn, and on its turn it asks the converser for one move.

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
import time
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

import structlog

from ... import world_pb2 as pb
from ..actions import converse_intent, give_intent
from ..geometry import Coord
from ..journal import append_scratch_line, read_journal
from ..outcomes import Seat, parse_gave, parse_seat
from ..tracelog import AgentTrace
from ..walk import WalkDriver, stand_candidates
from ..worldmodel import ConversationInfo, TickDigest, TranscriptLine, WorldModel
from .converser import run_converser
from .protocol import (
    ACTION_EAT,
    ACTION_GIVE,
    ACTION_JOIN,
    ACTION_LEAVE,
    ACTION_PASS,
    ACTION_SPEAK,
    CONVERSER_TIMEOUT_SECONDS,
    CONVERSE_ACTION_TYPE,
    DUPLICATE_TICK_WINDOW,
    END_CLOSED,
    END_DIED,
    END_LEFT,
    END_NOBODY_JOINED,
    END_REMOVED,
    GIVE_ACTION_TYPE,
    JOIN_ACTIONS,
    MAX_NOTE_CHARACTERS,
    MOVE_ACTION_TYPES,
    NOTE_STALE,
    NO_MOVE_ACTION,
    OBJECT_GRACE_TICKS,
    OUTCOME_NOT_CARRIED_OUT,
    OUTCOME_NO_KIND,
    OUTCOME_NO_TARGET,
    Converser,
    ConverserMove,
    MoveOutcome,
    NoteCall,
    PendingCall,
)
from .converser import NOTE_INSTRUCTION
from .report import ConversationReport, sleep_end_reason

logger = structlog.get_logger(__name__)


def free_seat_tiles(model: WorldModel, anchor: Coord) -> list[Coord]:
    """Walkable, unoccupied tiles next to `anchor`, nearest to the actor first.

    The anchor itself is excluded: the world requires a participant to stand
    beside it, not on it. One line over `walk.stand_candidates`, kept for the
    name the conversation code reads by.
    """
    return stand_candidates(model, anchor)


# The walk to a free seat is the shared one; the old name is what the planner
# and the tests call it.
ApproachDriver = WalkDriver


def joined_conversation(digest: TickDigest) -> Seat:
    """The seat the actor took on this tick, as `outcomes.Seat`.

    The world reports a successful `ConverseIntent` as an `EntityActed` with
    action type `converse` and details that start with the action name and the
    conversation id: `open conv_12`, `join conv_12`, `hail conv_12 mira` for
    the settler who hailed one, and `hailed conv_12 ivo` for the settler that
    hail seated.

    Every field is empty when the actor took no seat.
    """
    for acted in digest.own_actions:
        if acted.action_type != CONVERSE_ACTION_TYPE or not acted.success:
            continue
        seat = parse_seat(acted.details)
        if seat.conversation_id and seat.action in JOIN_ACTIONS:
            return seat
    return Seat()


def joined_conversation_id(digest: TickDigest) -> str:
    """The conversation the actor opened, joined or was seated in this tick."""
    return joined_conversation(digest).conversation_id


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
        purpose: str = "",
    ) -> None:
        self.conversation_id = conversation_id
        self.model = model
        self.converser = converser
        self.trace = trace
        self.memory_path = memory_path
        self.reflex_line = reflex_line
        self.alert_line = alert_line
        # Why this actor itself started the conversation (by opening or
        # hailing); empty for a settler that was hailed or that joined
        # (docs/09 section 10, item 4). Shown only in this actor's own prompt.
        self.purpose = purpose

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

    def begin(self, via: str = ACTION_JOIN) -> None:
        """Write the `conversation_start` trace line.

        `via` is how the seat was taken: `open`, `join`, `hail`, or `hailed`
        for the settler someone walked up to and addressed.
        """
        payload: dict[str, Any] = {
            "event": "conversation_start",
            "entity_id": self.model.entity_id,
            "tick": self.model.tick,
            "conversation_id": self.conversation_id,
            "via": via,
        }
        if self.purpose:
            payload["purpose"] = self.purpose
        self.trace.conversations.write(payload)

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
        self._trace_turn(
            result.move, result.latency_ms, result.error, usage=result.usage
        )
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
            self._trace_turn(
                result.move, result.latency_ms, NOTE_STALE, usage=result.usage
            )
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
            given = parse_gave(acted.details)
            if not given.happened or given.target != self.model.entity_id:
                continue
            self._received_items[given.kind] = (
                self._received_items.get(given.kind, 0) + given.amount
            )

    def _end_reason(self, conversation: ConversationInfo | None) -> str:
        if not self.model.self_info.alive:
            return END_DIED
        if self.model.self_info.asleep:
            return sleep_end_reason(self.model)
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
            f"Your health {info.health}/{info.max_health}, food "
            f"{info.food}/{info.max_food}, wielded "
            f"{info.wielded or 'nothing'}.\nYour pack: {inventory}.",
            "Transcript so far:\n" + self._transcript_text(conversation),
            self._moves_text(conversation.turn_started),
            "Your journal:\n" + read_notes(self.memory_path, self.model.entity_id),
            self.reflex_line(),
        ]
        if self.purpose:
            parts.append(f"You started this conversation because: {self.purpose}")
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
        # Traced here too (it was not before, docs/09 section 10, item 5):
        # the closing call is otherwise invisible to a run's traces.
        prompt = self.note_prompt(transcript)
        note_call = await self._ask_for_note(prompt)
        agreed = note_call.agreed
        commitment = note_call.commitment
        if agreed or commitment:
            append_conversation_note(
                self.memory_path,
                tick=self.model.tick,
                participants=self._other_participants(),
                agreed=agreed,
                commitment=commitment,
                entity_id=self.model.entity_id,
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
            agreed=agreed,
            commitment=commitment,
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
                "note_prompt": prompt,
                "agreed": agreed,
                "commitment": commitment,
                **({"usage": dict(note_call.usage)} if note_call.usage else {}),
            }
        )
        return report

    async def _ask_for_note(self, prompt: str) -> NoteCall:
        try:
            call = await self.converser.note(prompt)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - a failed note is not fatal
            # The conversation is already over; losing the note must not lose
            # the report the planner is waiting for.
            logger.warning("conversation_note_failed", error=str(error))
            return NoteCall()
        return NoteCall(
            call.agreed.strip()[:MAX_NOTE_CHARACTERS],
            call.commitment.strip()[:MAX_NOTE_CHARACTERS],
            call.usage,
        )

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
        usage: Mapping[str, Any] = MappingProxyType({}),
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
        if usage:
            payload["usage"] = dict(usage)
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


def read_notes(path: Path, entity_id: str = "") -> str:
    """The actor's journal, seeded on first read (docs/12_sleep_journal.md)."""
    return read_journal(path, entity_id)


def append_conversation_note(
    path: Path,
    *,
    tick: int,
    participants: Sequence[str],
    agreed: str = "",
    commitment: str = "",
    entity_id: str = "",
) -> None:
    """Append what was agreed/learned and what this settler said it would do.

    Each is its own `[conversation, tick N, with a, b] ...` line under today's
    notes when it is not empty (docs/09 section 10, item 5).
    """
    with_whom = ", ".join(participants) or "nobody"
    prefix = f"[conversation, tick {tick}, with {with_whom}]"
    if agreed:
        append_scratch_line(path, f"{prefix} agreed: {agreed}", entity_id)
    if commitment:
        append_scratch_line(path, f"{prefix} I said I would: {commitment}", entity_id)
