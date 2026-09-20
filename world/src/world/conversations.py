"""Conversations: world-enforced turn taking around an anchor tile.

Contract: docs/09_conversation_and_reflex.md, sections 2.1 to 2.4. A
conversation is a `WorldObject` of type `conversation` standing on its anchor
tile. It blocks nobody, belongs to neither building layer and cannot be
extracted; participants stand on tiles adjacent to the anchor.

Every function here is pure with respect to the frozen models: objects are
replaced through `World.update_object` and every state change is reported as an
`ObjectChange` so agents, the viewer, the recorder and the replay server all
see it through the machinery they already use.
"""

import json
from typing import Any, Mapping, Sequence

import structlog

from .events import (
    ObjectAddedEvent,
    ObjectChange,
    ObjectRemovedEvent,
    TickEvents,
    UtteranceEvent,
)
from .exceptions import InvalidObjectStateError, ObjectNotFoundError
from .items import (
    CONVERSATION,
    CONVERSATION_CHANNEL,
    CONVERSATION_LONELY_TICKS,
    CONVERSATION_MAX_PARTICIPANTS,
    CONVERSATION_MAX_UTTERANCES,
    CONVERSATION_TEXT_LIMIT,
    CONVERSATION_TRANSCRIPT_KEPT,
    CONVERSATION_TURN_TICKS,
    HAIL_COOLDOWN_TICKS,
)
from .state import WOLF_ENTITY_TYPE, World, WorldObject
from .types import (
    CONVERSE_HAIL,
    CONVERSE_JOIN,
    CONVERSE_LEAVE,
    CONVERSE_OPEN,
    CONVERSE_PASS,
    CONVERSE_SPEAK,
    LOCAL_CHANNEL,
    ConverseIntent,
    Position,
    is_adjacent,
)

logger = structlog.get_logger()

ACTION_TYPE = "converse"

# Object id prefix, so ids read as `conv_12` (docs/09, section 2.3).
CONVERSATION_ID_PREFIX = "conv"

# The word that opens the details of the hailed settler's own `EntityActed`,
# so its agent can tell a seat it never asked for from one it did (docs/09,
# section 9).
HAILED_DETAIL = "hailed"

# --- State keys -----------------------------------------------------------

PARTICIPANTS_KEY = "participants"
SPEAKER_KEY = "speaker"
TURN_STARTED_KEY = "turn_started"
OPENED_TICK_KEY = "opened_tick"
OPENED_BY_KEY = "opened_by"
UTTERANCES_KEY = "utterances"
TRANSCRIPT_KEY = "transcript"
# Consecutive passes (explicit or timed out); a full round closes the
# conversation. Not derivable from the other keys, so it is stored.
PASSES_KEY = "passes"

TranscriptLine = dict[str, Any]


# --- State helpers --------------------------------------------------------


def read_participants(obj: WorldObject) -> list[str]:
    """Participant entity ids in join order, opener first.

    Raises:
        InvalidObjectStateError: If the stored value is not a JSON list of ids.
    """
    raw = obj.get_state(PARTICIPANTS_KEY, "")
    if not raw:
        return []
    parsed = _load_json(obj, PARTICIPANTS_KEY, raw)
    if not isinstance(parsed, list) or not all(isinstance(e, str) for e in parsed):
        raise InvalidObjectStateError(
            f"Object {obj.object_id} participants is not a list of entity ids"
        )
    return list(parsed)


def read_transcript(obj: WorldObject) -> list[TranscriptLine]:
    """The kept transcript lines, oldest first.

    Raises:
        InvalidObjectStateError: If the stored value is not a JSON list.
    """
    raw = obj.get_state(TRANSCRIPT_KEY, "")
    if not raw:
        return []
    parsed = _load_json(obj, TRANSCRIPT_KEY, raw)
    if not isinstance(parsed, list) or not all(isinstance(e, dict) for e in parsed):
        raise InvalidObjectStateError(
            f"Object {obj.object_id} transcript is not a list of lines"
        )
    return list(parsed)


def read_count(obj: WorldObject, key: str) -> int:
    """Read an integer state value, defaulting to 0 when unset.

    Raises:
        InvalidObjectStateError: If the stored value is not an integer.
    """
    raw = obj.get_state(key, "")
    if not raw:
        return 0
    try:
        return int(raw)
    except ValueError as exc:
        raise InvalidObjectStateError(
            f"Object {obj.object_id} {key} is not an integer: {raw!r}"
        ) from exc


def _load_json(obj: WorldObject, key: str, raw: str) -> Any:
    """Parse one JSON state value.

    Raises:
        InvalidObjectStateError: If the value is not valid JSON.
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidObjectStateError(
            f"Object {obj.object_id} has unparsable {key}: {exc}"
        ) from exc


def _encode(value: Any) -> str:
    """Compact JSON for a stored list."""
    return json.dumps(value, separators=(",", ":"))


def truncate_line(text: str) -> str:
    """Trim a spoken line to the character limit."""
    return text[:CONVERSATION_TEXT_LIMIT]


# --- Lookups --------------------------------------------------------------


def all_conversations(world: World) -> list[WorldObject]:
    """Every conversation object, ordered by object id."""
    return sorted(
        (
            obj
            for obj in world.all_objects().values()
            if obj.object_type == CONVERSATION
        ),
        key=lambda obj: obj.object_id,
    )


def conversation_at(world: World, position: Position) -> WorldObject | None:
    """The conversation anchored on `position`, if any."""
    for obj in world.get_objects_at(position):
        if obj.object_type == CONVERSATION:
            return obj
    return None


def conversation_of(world: World, entity_id: str) -> WorldObject | None:
    """The conversation `entity_id` currently sits in, if any."""
    for obj in all_conversations(world):
        if entity_id in read_participants(obj):
            return obj
    return None


def share_conversation(world: World, a_id: str, b_id: str) -> bool:
    """True when both entities are participants in the same conversation."""
    conversation = conversation_of(world, a_id)
    return conversation is not None and b_id in read_participants(conversation)


# --- Writing ---------------------------------------------------------------


def _commit(
    world: World, before: WorldObject, after: WorldObject, events: TickEvents
) -> WorldObject:
    """Store `after` and report one ObjectChange per changed state key."""
    if before.state == after.state:
        return before
    world.update_object(after)
    old_state = dict(before.state)
    for key, new_value in after.state:
        old_value = old_state.get(key, "")
        if old_value != new_value:
            events.object_changes.append(
                ObjectChange(
                    object_id=after.object_id,
                    field=key,
                    old_value=old_value,
                    new_value=new_value,
                )
            )
    return after


def _with_updates(obj: WorldObject, updates: Mapping[str, str]) -> WorldObject:
    """Return a copy of `obj` with several state keys replaced."""
    updated = obj
    for key, value in updates.items():
        updated = updated.with_state(key, value)
    return updated


def _stamp_conversation_end(world: World, entity_ids: Sequence[str]) -> None:
    """Start the hail cooldown for settlers whose seat has just ended."""
    for entity_id in entity_ids:
        entity = world.all_entities().get(entity_id)
        if entity is None:
            continue
        world.set_entity(entity.with_conversation_ended(world.tick))


def _close(world: World, obj: WorldObject, reason: str, events: TickEvents) -> None:
    """Remove a conversation object and report it."""
    _stamp_conversation_end(world, read_participants(obj))
    world.remove_object(obj.object_id)
    events.objects_removed.append(
        ObjectRemovedEvent(object_id=obj.object_id, position=obj.position)
    )
    logger.info("conversation_closed", conversation_id=obj.object_id, reason=reason)


# --- Turn order ------------------------------------------------------------


def _next_speaker(participants: list[str], current: str) -> str:
    """Speaker after `current` in round-robin join order."""
    if not participants:
        return ""
    if current not in participants:
        return participants[0]
    index = participants.index(current)
    return participants[(index + 1) % len(participants)]


def _advance_turn(
    world: World, obj: WorldObject, passed: bool, events: TickEvents
) -> WorldObject:
    """Hand the turn to the next participant, tracking the pass streak."""
    participants = read_participants(obj)
    passes = read_count(obj, PASSES_KEY) + 1 if passed else 0
    updates = {
        SPEAKER_KEY: _next_speaker(participants, obj.get_state(SPEAKER_KEY, "")),
        TURN_STARTED_KEY: str(world.tick),
        PASSES_KEY: str(passes),
    }
    return _commit(world, obj, _with_updates(obj, updates), events)


# --- Intent handling -------------------------------------------------------


def _fail(events: TickEvents, entity_id: str, reason: str) -> None:
    """Record one failed converse action."""
    events.acted(entity_id, ACTION_TYPE, False, reason)


def _open_conversation(
    world: World, intent: ConverseIntent, events: TickEvents
) -> None:
    """Handle one `open` action (docs/09, section 2.3)."""
    entity_id = intent.entity_id
    entity = world.get_entity(entity_id)

    if conversation_of(world, entity_id) is not None:
        _fail(events, entity_id, "already in a conversation")
        return
    if intent.direction is None:
        _fail(events, entity_id, "open needs a direction")
        return
    text = truncate_line(intent.text.strip())
    if not text:
        _fail(events, entity_id, "opening line is required")
        return

    anchor = entity.position.offset(intent.direction)
    if not world.in_bounds(anchor):
        _fail(events, entity_id, "anchor is out of bounds")
        return
    if not world.is_walkable(anchor):
        _fail(events, entity_id, "anchor is not walkable")
        return
    if world.is_blocked(anchor):
        _fail(events, entity_id, "anchor holds a blocking object")
        return
    if world.is_position_occupied(anchor):
        _fail(events, entity_id, "anchor is occupied by an entity")
        return
    if conversation_at(world, anchor) is not None:
        _fail(events, entity_id, "anchor already holds a conversation")
        return

    tick = str(world.tick)
    opening = {"tick": world.tick, "speaker": entity_id, "text": text}
    obj = WorldObject(
        object_id=world.generate_object_id(CONVERSATION_ID_PREFIX),
        position=anchor,
        object_type=CONVERSATION,
        state=(
            (PARTICIPANTS_KEY, _encode([entity_id])),
            (SPEAKER_KEY, ""),
            (TURN_STARTED_KEY, tick),
            (OPENED_TICK_KEY, tick),
            (OPENED_BY_KEY, entity_id),
            (UTTERANCES_KEY, "0"),
            (PASSES_KEY, "0"),
            (TRANSCRIPT_KEY, _encode([opening])),
        ),
    )
    world.add_object(obj)
    events.objects_added.append(ObjectAddedEvent(obj=obj))
    # The opening line goes out on the local channel so bystanders hear it and
    # learn which conversation it belongs to.
    events.utterances.append(
        UtteranceEvent(
            speaker_id=entity_id,
            channel=LOCAL_CHANNEL,
            text=text,
            position=entity.position,
            conversation_id=obj.object_id,
        )
    )
    events.acted(entity_id, ACTION_TYPE, True, f"open {obj.object_id}")


def _shared_anchor(world: World, a: Position, b: Position) -> Position | None:
    """A free tile adjacent to both positions, in ascending (x, y) order."""
    candidates = sorted(
        (
            Position(x=a.x + dx, y=a.y + dy)
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            if (dx, dy) != (0, 0)
        ),
        key=lambda position: (position.x, position.y),
    )
    for anchor in candidates:
        if not is_adjacent(anchor, b):
            continue
        if not world.in_bounds(anchor) or not world.is_walkable(anchor):
            continue
        if world.is_blocked(anchor) or world.is_position_occupied(anchor):
            continue
        if conversation_at(world, anchor) is not None:
            continue
        return anchor
    return None


def _hail(world: World, intent: ConverseIntent, events: TickEvents) -> None:
    """Handle one `hail` action (docs/09, section 9).

    A hail is one settler walking up to another and addressing it: the hailer
    opens the conversation with its line and the target speaks next.
    """
    entity_id = intent.entity_id
    entity = world.get_entity(entity_id)
    target_id = intent.target_entity_id

    text = truncate_line(intent.text.strip())
    if not text:
        _fail(events, entity_id, "an opening line is required")
        return

    target = world.all_entities().get(target_id)
    if target is None or target.entity_type == WOLF_ENTITY_TYPE:
        _fail(events, entity_id, f"there is no settler called {target_id}")
        return
    if not target.alive:
        _fail(events, entity_id, f"{target_id} is dead")
        return
    if entity.asleep:
        _fail(events, entity_id, "you are asleep")
        return
    if target.asleep:
        _fail(events, entity_id, f"{target_id} is asleep")
        return
    if conversation_of(world, entity_id) is not None:
        _fail(events, entity_id, "already in a conversation")
        return
    seated = conversation_of(world, target_id)
    if seated is not None:
        _fail(
            events,
            entity_id,
            f"{target_id} is already in conversation {seated.object_id}",
        )
        return
    if not is_adjacent(entity.position, target.position):
        _fail(events, entity_id, f"not next to {target_id}")
        return
    cooldown_left = target.hail_cooldown_left(world.tick, HAIL_COOLDOWN_TICKS)
    if cooldown_left > 0:
        ago = HAIL_COOLDOWN_TICKS - cooldown_left
        _fail(
            events,
            entity_id,
            f"{target_id} was in a conversation {ago} ticks ago and cannot be "
            f"hailed for another {cooldown_left} ticks",
        )
        return

    anchor = _shared_anchor(world, entity.position, target.position)
    if anchor is None:
        _fail(events, entity_id, "no free tile next to you both")
        return

    tick = str(world.tick)
    opening = {"tick": world.tick, "speaker": entity_id, "text": text}
    obj = WorldObject(
        object_id=world.generate_object_id(CONVERSATION_ID_PREFIX),
        position=anchor,
        object_type=CONVERSATION,
        state=(
            (PARTICIPANTS_KEY, _encode([entity_id, target_id])),
            # The hailer has already spoken, so the turn is the target's.
            (SPEAKER_KEY, target_id),
            (TURN_STARTED_KEY, tick),
            (OPENED_TICK_KEY, tick),
            (OPENED_BY_KEY, entity_id),
            (UTTERANCES_KEY, "0"),
            (PASSES_KEY, "0"),
            (TRANSCRIPT_KEY, _encode([opening])),
        ),
    )
    world.add_object(obj)
    events.objects_added.append(ObjectAddedEvent(obj=obj))
    # The opening line is ordinary local speech as well, so bystanders hear it
    # and learn which conversation it belongs to (as `open` does).
    events.utterances.append(
        UtteranceEvent(
            speaker_id=entity_id,
            channel=LOCAL_CHANNEL,
            text=text,
            position=entity.position,
            conversation_id=obj.object_id,
        )
    )
    events.acted(
        entity_id, ACTION_TYPE, True, f"{CONVERSE_HAIL} {obj.object_id} {target_id}"
    )
    # The target is seated without asking, and its own details say so.
    events.acted(
        target_id, ACTION_TYPE, True, f"{HAILED_DETAIL} {obj.object_id} {entity_id}"
    )


def _join_conversation(
    world: World, intent: ConverseIntent, events: TickEvents
) -> None:
    """Handle one `join` action (docs/09, section 2.3)."""
    entity_id = intent.entity_id
    entity = world.get_entity(entity_id)

    if conversation_of(world, entity_id) is not None:
        _fail(events, entity_id, "already in a conversation")
        return
    try:
        obj = world.get_object(intent.conversation_id)
    except ObjectNotFoundError:
        _fail(events, entity_id, f"no conversation {intent.conversation_id}")
        return
    if obj.object_type != CONVERSATION:
        _fail(events, entity_id, f"{obj.object_id} is not a conversation")
        return
    if not is_adjacent(entity.position, obj.position):
        _fail(events, entity_id, "not adjacent to the conversation")
        return

    participants = read_participants(obj)
    if len(participants) >= CONVERSATION_MAX_PARTICIPANTS:
        _fail(events, entity_id, "conversation is full")
        return

    participants.append(entity_id)
    updates = {PARTICIPANTS_KEY: _encode(participants)}
    # The opener speaks first, from the tick the second participant arrives.
    if len(participants) == 2:
        updates[SPEAKER_KEY] = participants[0]
        updates[TURN_STARTED_KEY] = str(world.tick)
        updates[PASSES_KEY] = "0"
    _commit(world, obj, _with_updates(obj, updates), events)
    events.acted(entity_id, ACTION_TYPE, True, f"join {obj.object_id}")


def _speak(world: World, intent: ConverseIntent, events: TickEvents) -> None:
    """Handle one `speak` action (docs/09, section 2.3)."""
    entity_id = intent.entity_id
    obj = conversation_of(world, entity_id)
    if obj is None:
        _fail(events, entity_id, "not in a conversation")
        return
    if obj.get_state(SPEAKER_KEY, "") != entity_id:
        _fail(events, entity_id, "not your turn")
        return
    text = truncate_line(intent.text.strip())
    if not text:
        _fail(events, entity_id, "text is required")
        return

    entity = world.get_entity(entity_id)
    transcript = read_transcript(obj)
    transcript.append({"tick": world.tick, "speaker": entity_id, "text": text})
    updates = {
        TRANSCRIPT_KEY: _encode(transcript[-CONVERSATION_TRANSCRIPT_KEPT:]),
        UTTERANCES_KEY: str(read_count(obj, UTTERANCES_KEY) + 1),
    }
    obj = _commit(world, obj, _with_updates(obj, updates), events)
    _advance_turn(world, obj, passed=False, events=events)

    events.utterances.append(
        UtteranceEvent(
            speaker_id=entity_id,
            channel=CONVERSATION_CHANNEL,
            text=text,
            position=entity.position,
            conversation_id=obj.object_id,
        )
    )
    events.acted(entity_id, ACTION_TYPE, True, "speak")


def _pass_turn(world: World, intent: ConverseIntent, events: TickEvents) -> None:
    """Handle one `pass` action (docs/09, section 2.3)."""
    entity_id = intent.entity_id
    obj = conversation_of(world, entity_id)
    if obj is None:
        _fail(events, entity_id, "not in a conversation")
        return
    if obj.get_state(SPEAKER_KEY, "") != entity_id:
        _fail(events, entity_id, "not your turn")
        return
    _advance_turn(world, obj, passed=True, events=events)
    events.acted(entity_id, ACTION_TYPE, True, "pass")


def _leave(world: World, intent: ConverseIntent, events: TickEvents) -> None:
    """Handle one `leave` action; the lifecycle pass does the removal."""
    entity_id = intent.entity_id
    obj = conversation_of(world, entity_id)
    if obj is None:
        _fail(events, entity_id, "not in a conversation")
        return
    _remove_participants(world, obj, {entity_id}, events)
    events.acted(entity_id, ACTION_TYPE, True, "leave")


def _remove_participants(
    world: World, obj: WorldObject, leaving: set[str], events: TickEvents
) -> WorldObject:
    """Drop entities from the seat list, moving the turn on if needed."""
    participants = read_participants(obj)
    remaining = [p for p in participants if p not in leaving]
    if len(remaining) == len(participants):
        return obj

    _stamp_conversation_end(world, [p for p in participants if p in leaving])
    updates = {PARTICIPANTS_KEY: _encode(remaining)}
    speaker = obj.get_state(SPEAKER_KEY, "")
    if speaker in leaving:
        # Hand the turn to the next participant that is still seated.
        successor = _next_speaker(participants, speaker)
        while successor in leaving and successor != speaker:
            successor = _next_speaker(participants, successor)
        updates[SPEAKER_KEY] = successor if successor in remaining else ""
        updates[TURN_STARTED_KEY] = str(world.tick)
    return _commit(world, obj, _with_updates(obj, updates), events)


# --- Lifecycle -------------------------------------------------------------


def _absent_participants(world: World, obj: WorldObject) -> set[str]:
    """Participants that died, vanished, or walked away from the anchor."""
    absent: set[str] = set()
    for entity_id in read_participants(obj):
        entity = world.all_entities().get(entity_id)
        if entity is None or not entity.alive:
            absent.add(entity_id)
            continue
        if not is_adjacent(entity.position, obj.position):
            absent.add(entity_id)
    return absent


def _closing_reason(world: World, obj: WorldObject) -> str:
    """Why this conversation must close now, or "" when it lives on."""
    participants = read_participants(obj)
    speaker = obj.get_state(SPEAKER_KEY, "")
    had_two = speaker != "" or len(participants) >= 2
    if not participants:
        return "empty"
    if had_two and len(participants) < 2:
        return "alone"
    if read_count(obj, UTTERANCES_KEY) >= CONVERSATION_MAX_UTTERANCES:
        return "utterance_cap"
    if len(participants) >= 2 and read_count(obj, PASSES_KEY) >= len(participants):
        return "all_passed"
    if not had_two:
        opened = read_count(obj, OPENED_TICK_KEY)
        if world.tick - opened >= CONVERSATION_LONELY_TICKS:
            return "nobody_joined"
    return ""


def _advance_lifecycle(world: World, obj: WorldObject, events: TickEvents) -> None:
    """Apply removals, the turn timeout and the closing rules to one object."""
    current = _remove_participants(world, obj, _absent_participants(world, obj), events)

    participants = read_participants(current)
    speaker = current.get_state(SPEAKER_KEY, "")
    timed_out = (
        len(participants) >= 2
        and speaker != ""
        and world.tick - read_count(current, TURN_STARTED_KEY)
        >= CONVERSATION_TURN_TICKS
    )
    if timed_out:
        current = _advance_turn(world, current, passed=True, events=events)

    reason = _closing_reason(world, current)
    if reason:
        _close(world, current, reason, events)


def process_conversation_phase(
    world: World,
    intents: Mapping[str, ConverseIntent],
    events: TickEvents,
) -> None:
    """Resolve converse intents, then run every conversation's lifecycle.

    Runs once per tick after movement and combat. Conflicts (two `open`
    intents for one anchor, more `join` intents than free seats) resolve by
    lexicographic entity id, like every other conflict in the world.
    """
    by_action: dict[str, list[ConverseIntent]] = {}
    for entity_id in sorted(intents):
        intent = intents[entity_id]
        by_action.setdefault(intent.action, []).append(intent)

    for intent in by_action.get(CONVERSE_OPEN, ()):
        _open_conversation(world, intent, events)
    for intent in by_action.get(CONVERSE_HAIL, ()):
        _hail(world, intent, events)
    for intent in by_action.get(CONVERSE_JOIN, ()):
        _join_conversation(world, intent, events)
    for intent in by_action.get(CONVERSE_SPEAK, ()):
        _speak(world, intent, events)
    for intent in by_action.get(CONVERSE_PASS, ()):
        _pass_turn(world, intent, events)
    for intent in by_action.get(CONVERSE_LEAVE, ()):
        _leave(world, intent, events)

    for obj in all_conversations(world):
        _advance_lifecycle(world, obj, events)


__all__ = [
    "CONVERSATION_ID_PREFIX",
    "HAILED_DETAIL",
    "OPENED_BY_KEY",
    "OPENED_TICK_KEY",
    "PARTICIPANTS_KEY",
    "PASSES_KEY",
    "SPEAKER_KEY",
    "TRANSCRIPT_KEY",
    "TURN_STARTED_KEY",
    "UTTERANCES_KEY",
    "all_conversations",
    "conversation_at",
    "conversation_of",
    "process_conversation_phase",
    "read_count",
    "read_participants",
    "read_transcript",
    "share_conversation",
    "truncate_line",
]
