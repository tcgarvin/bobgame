"""Agent-side memory of everything this actor has ever observed.

The world server only tells an agent about a 17x17 window each tick. The
`WorldModel` accumulates those windows so the planner and the pathfinder can
reason about places the actor cannot currently see, while still forgetting
things that demonstrably went away (an object inside the current view that is
no longer reported has been removed).
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from .. import world_pb2 as pb
from . import items
from .geometry import Coord, chebyshev, direction_name

VIEW_RADIUS = 8

# Object types that occupy their tile. Walls really do block (the world says so
# in `Tile.walkable` too); trees and rocks are treated as blocking only because
# the agent stays conservative - a slightly longer path costs little. Doors are
# deliberately absent: settlers walk through them, only wolves cannot.
BLOCKING_OBJECT_TYPES: frozenset[str] = (
    frozenset({items.TREE}) | items.ROCK_TYPES | items.BLOCKING_OBJECT_TYPES
)

# Re-exported so the rest of the agent can keep importing them from here.
ROCK_TYPES = items.ROCK_TYPES
EXTRACTABLE_TYPES = items.EXTRACTABLE_TYPES
DEFAULT_REMAINING: Mapping[str, int] = items.DEFAULT_REMAINING

HISTORY_LIMIT = 200
UTTERANCE_LIMIT = 40
DAMAGE_LOG_LIMIT = 40


@dataclass(frozen=True)
class TileInfo:
    """A tile as it was last observed."""

    position: Coord
    walkable: bool
    opaque: bool
    floor_type: str
    last_seen: int


@dataclass(frozen=True)
class WorldClock:
    """The world's day clock, as the observation reports it (docs/10 section 3)."""

    day: int = 0
    tick_of_day: int = 0
    day_length: int = items.DEFAULT_DAY_LENGTH
    night: bool = False

    def as_text(self) -> str:
        """`"day 2 212/300 night"`, the form every tick line uses."""
        return (
            f"day {self.day} {self.tick_of_day}/{self.day_length} "
            f"{'night' if self.night else 'day'}"
        )


@dataclass(frozen=True)
class ObjectInfo:
    """A world object as it was last observed."""

    object_id: str
    object_type: str
    position: Coord
    state: Mapping[str, str]
    last_seen: int

    @property
    def remaining(self) -> int:
        """Units of material left, falling back to the type's default."""
        raw = self.state.get("remaining", "")
        if raw.isdigit():
            return int(raw)
        return DEFAULT_REMAINING.get(self.object_type, 0)

    @property
    def owner(self) -> str:
        """Who placed this object; `""` for anything the world grew or dropped."""
        return self.state.get(items.OWNER_KEY, "")

    @property
    def has_berry(self) -> bool:
        """Whether this bush currently carries a berry."""
        return self.state.get("berry_count", "0") not in ("", "0")

    def contents(self) -> dict[str, int]:
        """Parsed `contents` JSON for chests and item piles ({} when absent/bad)."""
        return _parse_counts(self.state.get("contents", ""))

    def craft_progress(self, entity_id: str) -> tuple[str, int]:
        """`(recipe, actions done)` this crafter has banked at this station.

        Empty recipe and zero when the station holds no progress for them, or
        when the state value is not the `"<recipe>:<done>"` the world writes.
        """
        raw = self.state.get(f"{items.CRAFT_PROGRESS_PREFIX}{entity_id}", "")
        recipe, _, done = raw.partition(":")
        if not recipe or not done.isdigit():
            return ("", 0)
        return (recipe, int(done))

    @property
    def sign_text(self) -> str:
        """The one line a sign carries; `""` for a blank sign or anything else."""
        return self.state.get(items.SIGN_TEXT_KEY, "")

    @property
    def sign_author(self) -> str:
        """Who wrote this sign's current line, or `""` when it is blank."""
        return self.state.get(items.SIGN_AUTHOR_KEY, "")

    @property
    def sign_tick(self) -> int:
        """The tick this sign's current line was written; 0 when it is blank."""
        raw = self.state.get(items.SIGN_TICK_KEY, "")
        return int(raw) if raw.isdigit() else 0

    def notes(self) -> list[dict[str, object]]:
        """Parsed `notes` JSON for message boards, with empty slots dropped."""
        raw = self.state.get("notes", "")
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return [note for note in parsed if isinstance(note, dict)]

    def notes_by_slot(self) -> dict[int, dict[str, object]]:
        """Parsed `notes` JSON for message boards, keyed by their real slot.

        `notes()` drops empty slots, so its list index is not the slot number
        `write_note` and `read_board` use; this keeps that number so read
        tracking survives other slots filling or emptying.
        """
        raw = self.state.get("notes", "")
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(parsed, list):
            return {}
        return {
            index: note for index, note in enumerate(parsed) if isinstance(note, dict)
        }


@dataclass(frozen=True)
class EntityInfo:
    """An entity as it was last observed."""

    entity_id: str
    entity_type: str
    position: Coord
    health: int
    max_health: int
    food: int
    max_food: int
    wielded: str
    alive: bool
    inventory: Mapping[str, int]
    last_seen: int
    fatigue: int = 0
    max_fatigue: int = items.MAX_FATIGUE
    asleep: bool = False
    # True while this entity's invitation to talk is still open: anyone next to
    # it may accept and a conversation starts (docs/09 section 8).
    open_to_talk: bool = False

    @property
    def fatigue_word(self) -> str:
        """`fresh`, `tired` or `exhausted` for this entity's fatigue."""
        return items.fatigue_word(self.fatigue)


@dataclass(frozen=True)
class HistoryEntry:
    """One line of the actor's own past: what it tried and what came of it."""

    tick: int
    text: str


@dataclass(frozen=True)
class HeardUtterance:
    """Something an actor said within earshot, and where they stood.

    `conversation_id` is empty for ordinary speech; it names the conversation
    for a line spoken in one, and for the opening line, which is heard on the
    local channel. `open_to_talk` is set when the speaker said it as an
    invitation, so it stays open to an accept for `INVITATION_TICKS`.
    """

    tick: int
    speaker_id: str
    channel: str
    text: str
    position: Coord
    conversation_id: str = ""
    open_to_talk: bool = False


@dataclass(frozen=True)
class TranscriptLine:
    """One line of a conversation, as the actor heard it or as state records it."""

    tick: int
    speaker: str
    text: str

    def as_line(self) -> str:
        """`t12 mira: hello`, the form both prompts and reports use."""
        return f"t{self.tick} {self.speaker}: {self.text}"


@dataclass(frozen=True)
class ConversationInfo:
    """A `conversation` object, parsed out of its string state.

    The world's contract is docs/09_conversation_and_reflex.md section 2.1;
    every value in object state is a string, so everything here is parsed
    defensively and falls back to an empty or zero value.
    """

    conversation_id: str
    anchor: Coord
    participants: tuple[str, ...]
    speaker: str
    turn_started: int
    opened_tick: int
    opened_by: str
    utterances: int
    transcript: tuple[TranscriptLine, ...]

    @property
    def free_seats(self) -> int:
        """Seats still open at this conversation."""
        return max(0, items.CONVERSATION_MAX_PARTICIPANTS - len(self.participants))

    def has(self, entity_id: str) -> bool:
        """Whether `entity_id` currently holds a seat."""
        return entity_id in self.participants

    def summary(self) -> str:
        """One line for `look`: who is there and how many seats are free."""
        seated = ", ".join(self.participants) or "nobody"
        return (
            f"{self.conversation_id} at {self.anchor}: {seated} "
            f"({self.free_seats} free seats, {self.utterances} lines said)"
        )


def _parse_participants(raw: str) -> tuple[str, ...]:
    """Parse the `participants` JSON list; a bad value means nobody."""
    if not raw:
        return ()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(str(entry) for entry in parsed)


def _parse_transcript(raw: str) -> tuple[TranscriptLine, ...]:
    """Parse the `transcript` JSON list; malformed entries are dropped."""
    if not raw:
        return ()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, list):
        return ()
    lines: list[TranscriptLine] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        tick = entry.get("tick", 0)
        lines.append(
            TranscriptLine(
                tick=tick if isinstance(tick, int) else 0,
                speaker=str(entry.get("speaker", "")),
                text=str(entry.get("text", "")),
            )
        )
    return tuple(lines)


def _parse_int(raw: str) -> int:
    """A non-negative integer from object state; anything else is 0."""
    return int(raw) if raw.isdigit() else 0


def conversation_from_object(obj: ObjectInfo) -> ConversationInfo:
    """Build a `ConversationInfo` from a `conversation` world object."""
    state = obj.state
    return ConversationInfo(
        conversation_id=obj.object_id,
        anchor=obj.position,
        participants=_parse_participants(state.get("participants", "")),
        speaker=state.get("speaker", ""),
        turn_started=_parse_int(state.get("turn_started", "")),
        opened_tick=_parse_int(state.get("opened_tick", "")),
        opened_by=state.get("opened_by", ""),
        utterances=_parse_int(state.get("utterances", "")),
        transcript=_parse_transcript(state.get("transcript", "")),
    )


@dataclass(frozen=True)
class DamageTaken:
    """One hit this actor took; `attacker_id` is empty for starvation."""

    tick: int
    amount: int
    attacker_id: str


@dataclass
class TickDigest:
    """What changed for this actor during the tick just observed."""

    tick: int = 0
    own_actions: list[pb.EntityActed] = field(default_factory=list)
    # Actions by other entities that this actor was shown, such as a `give`
    # aimed at it. The world only sends these when they concern the actor.
    others_actions: list[pb.EntityActed] = field(default_factory=list)
    damage_taken: int = 0
    attackers: list[str] = field(default_factory=list)
    deaths: list[str] = field(default_factory=list)
    utterances: list[HeardUtterance] = field(default_factory=list)
    discovered_object_ids: list[str] = field(default_factory=list)
    self_died: bool = False
    self_respawned: bool = False
    # One line per sign whose text this actor is being shown for the first
    # time (docs/08_building.md, "Signs"). Already formatted for the planner.
    sign_notes: list[str] = field(default_factory=list)
    # One line per message board note by another author this actor is seeing
    # for the first time (docs/09_conversation_and_reflex.md), on the same
    # note path as `sign_notes`. Independent of `read_board`: it fires just by
    # being in view, `read_board` only clears the `look` "(new)" marker.
    board_notes: list[str] = field(default_factory=list)


def _parse_counts(raw: str) -> dict[str, int]:
    """Parse a `{"kind": count}` JSON blob from object state."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    counts: dict[str, int] = {}
    for kind, value in parsed.items():
        if isinstance(value, int) and value > 0:
            counts[str(kind)] = value
    return counts


def sign_note_line(sign: ObjectInfo) -> str:
    """The one line a settler is shown when it reads a sign.

    `[sign at (12, 30) by ada, written tick 91: "wolves north"]`.
    """
    return (
        f"[sign at {sign.position} by {sign.sign_author or 'nobody'}, "
        f'written tick {sign.sign_tick}: "{sign.sign_text}"]'
    )


def inventory_to_dict(inventory: pb.Inventory) -> dict[str, int]:
    """Flatten a proto Inventory into `{kind: quantity}`, dropping empties."""
    return {item.kind: item.quantity for item in inventory.items if item.quantity > 0}


class WorldModel:
    """Everything this actor has seen, indexed for fast lookup."""

    def __init__(self, entity_id: str) -> None:
        self.entity_id = entity_id
        self.tick = 0
        self.tiles: dict[Coord, TileInfo] = {}
        self.objects: dict[str, ObjectInfo] = {}
        self.entities: dict[str, EntityInfo] = {}
        self.self_info = EntityInfo(
            entity_id=entity_id,
            entity_type="player",
            position=(0, 0),
            health=0,
            max_health=0,
            food=0,
            max_food=0,
            wielded="",
            alive=True,
            inventory={},
            last_seen=0,
        )
        # The spawn site doubles as the settlement centre: every actor spawns
        # and respawns there. The observation stream carries no settlement
        # field, so the first observed position is the best available estimate.
        self.settlement: Coord = (0, 0)
        self.settlement_known = False
        self.clock = WorldClock()
        self.history: deque[HistoryEntry] = deque(maxlen=HISTORY_LIMIT)
        self.heard: deque[HeardUtterance] = deque(maxlen=UTTERANCE_LIMIT)
        self.damage_log: deque[DamageTaken] = deque(maxlen=DAMAGE_LOG_LIMIT)
        # Everything this actor heard inside a conversation, kept per
        # conversation: `heard` is a short shared window, and a converser needs
        # the whole exchange it sat through.
        self.conversation_lines: dict[str, list[TranscriptLine]] = {}
        # sign object id -> the text this actor has already been shown. A sign
        # is pushed at whoever walks past, once per text (docs/08_building.md).
        self.sign_texts_read: dict[str, str] = {}
        # board object id -> {slot: tick} for the note version this actor last
        # read with `read_board`. Drives the `look` "(new)" marker; a note
        # only comes off it by being read, not by walking past.
        self.board_notes_read: dict[str, dict[int, int]] = {}
        # board object id -> {slot: tick} for the note version this actor has
        # already been pushed a `board_notes` line for, so a note by someone
        # else is announced once, not on every tick it stays in view.
        self._board_notes_notified: dict[str, dict[int, int]] = {}
        self.last_digest = TickDigest()
        # Position indexes rebuilt once per update() so that pathfinding's
        # walkability checks are O(1) instead of scanning every known object.
        self._blocked_positions: set[Coord] = set()
        self._occupied_positions: set[Coord] = set()

    # -- terrain view (satisfies pathfinding.TerrainView) -------------------

    def is_known(self, position: Coord) -> bool:
        """Whether this tile has ever been observed."""
        return position in self.tiles

    def is_walkable(self, position: Coord) -> bool:
        """Whether an entity may stand here. Unobserved tiles count as walkable."""
        tile = self.tiles.get(position)
        if tile is not None and not tile.walkable:
            return False
        if position in self._blocked_positions:
            return False
        # Other entities seen this very tick block the tile for now; the world
        # refuses moves onto an occupied tile unless the occupant moves away.
        return position not in self._occupied_positions

    def _rebuild_position_indexes(self) -> None:
        self._blocked_positions = {
            obj.position
            for obj in self.objects.values()
            if obj.object_type in BLOCKING_OBJECT_TYPES
        }
        self._occupied_positions = {
            entity.position
            for entity in self.entities.values()
            if entity.last_seen == self.tick and entity.alive
        }

    def note_own_outcome(self, text: str) -> None:
        """Record an outcome the world did not report as an event (e.g. a blocked move)."""
        self.history.append(HistoryEntry(self.tick, f"t{self.tick} {text}"))

    # -- updating -----------------------------------------------------------

    def update(self, observation: pb.Observation) -> TickDigest:
        """Fold one observation into memory and return what changed."""
        self.tick = observation.tick_id
        digest = TickDigest(tick=observation.tick_id)

        self.self_info = _entity_info(observation.self, observation.tick_id)
        self.clock = _world_clock(observation.clock)
        if not self.settlement_known:
            self.settlement = self.self_info.position
            self.settlement_known = True

        for tile in observation.visible_tiles:
            position = (tile.position.x, tile.position.y)
            self.tiles[position] = TileInfo(
                position=position,
                walkable=tile.walkable,
                opaque=tile.opaque,
                floor_type=tile.floor_type,
                last_seen=observation.tick_id,
            )

        self._refresh_objects(observation, digest)
        self._refresh_entities(observation)
        self._apply_events(observation, digest)
        self._read_signs_in_view(digest)
        self._check_boards_in_view(digest)
        self._rebuild_position_indexes()

        self.last_digest = digest
        return digest

    def _refresh_objects(self, observation: pb.Observation, digest: TickDigest) -> None:
        centre = self.self_info.position
        visible_ids = {obj.object_id for obj in observation.visible_objects}
        for object_id in [
            object_id
            for object_id, obj in self.objects.items()
            if object_id not in visible_ids
            and chebyshev(obj.position, centre) <= VIEW_RADIUS
        ]:
            del self.objects[object_id]

        for obj in observation.visible_objects:
            if obj.object_id not in self.objects:
                digest.discovered_object_ids.append(obj.object_id)
            self.objects[obj.object_id] = ObjectInfo(
                object_id=obj.object_id,
                object_type=obj.object_type,
                position=(obj.position.x, obj.position.y),
                state=dict(obj.state),
                last_seen=observation.tick_id,
            )

    def _refresh_entities(self, observation: pb.Observation) -> None:
        centre = self.self_info.position
        visible_ids = {entity.entity_id for entity in observation.visible_entities}
        for entity_id in [
            entity_id
            for entity_id, entity in self.entities.items()
            if entity_id not in visible_ids
            and chebyshev(entity.position, centre) <= VIEW_RADIUS
        ]:
            del self.entities[entity_id]

        for entity in observation.visible_entities:
            self.entities[entity.entity_id] = _entity_info(entity, observation.tick_id)

    def _apply_events(self, observation: pb.Observation, digest: TickDigest) -> None:
        tick = observation.tick_id
        for event in observation.events:
            kind = event.WhichOneof("event")
            if kind == "entity_acted":
                acted = event.entity_acted
                if acted.entity_id != self.entity_id:
                    digest.others_actions.append(acted)
                    continue
                digest.own_actions.append(acted)
                outcome = "ok" if acted.success else "failed"
                detail = f" ({acted.details})" if acted.details else ""
                self.history.append(
                    HistoryEntry(tick, f"t{tick} {acted.action_type} {outcome}{detail}")
                )
            elif kind == "entity_moved":
                moved = event.entity_moved
                if moved.entity_id != self.entity_id:
                    continue
                # `from` is a Python keyword, so protobuf hides it behind getattr.
                origin: pb.Position = getattr(moved, "from")
                delta = (moved.to.x - origin.x, moved.to.y - origin.y)
                self.history.append(
                    HistoryEntry(tick, f"t{tick} move {_delta_name(delta)} ok")
                )
            elif kind == "utterance":
                utterance = event.utterance
                heard = HeardUtterance(
                    tick,
                    utterance.speaker_id,
                    utterance.channel,
                    utterance.text,
                    (utterance.position.x, utterance.position.y),
                    utterance.conversation_id,
                    utterance.open_to_talk,
                )
                self.heard.append(heard)
                digest.utterances.append(heard)
                if heard.conversation_id:
                    self._remember_conversation_line(heard)
            elif kind == "entity_damaged":
                damaged = event.entity_damaged
                if damaged.entity_id == self.entity_id:
                    digest.damage_taken += damaged.amount
                    self.damage_log.append(
                        DamageTaken(tick, damaged.amount, damaged.attacker_id)
                    )
                    if damaged.attacker_id:
                        digest.attackers.append(damaged.attacker_id)
            elif kind == "entity_died":
                died = event.entity_died
                digest.deaths.append(died.entity_id)
                if died.entity_id == self.entity_id:
                    digest.self_died = True
                    self.history.append(
                        HistoryEntry(tick, f"t{tick} died (killer {died.killer_id})")
                    )
                self.entities.pop(died.entity_id, None)
            elif kind == "entity_respawned":
                respawned = event.entity_respawned
                if respawned.entity_id == self.entity_id:
                    digest.self_respawned = True
                    # Respawn happens at the settlement spawn point.
                    self.settlement = (respawned.position.x, respawned.position.y)
                    self.settlement_known = True
            elif kind == "object_removed":
                self.objects.pop(event.object_removed.object_id, None)
            elif kind == "object_added":
                added = event.object_added.object
                self.objects[added.object_id] = ObjectInfo(
                    object_id=added.object_id,
                    object_type=added.object_type,
                    position=(added.position.x, added.position.y),
                    state=dict(added.state),
                    last_seen=tick,
                )

    def _read_signs_in_view(self, digest: TickDigest) -> None:
        """Queue a note for every sign in view whose line is new to this actor.

        A sign is the one channel nobody has to ask for: walking past a written
        sign delivers its line once, and again whenever the text changes. The
        actor's own signs are recorded as read without a note, so a settler is
        never told what it just wrote itself.
        """
        for obj in self.objects.values():
            if obj.object_type != items.SIGN or obj.last_seen != self.tick:
                continue
            text = obj.sign_text
            already = self.sign_texts_read.get(obj.object_id)
            self.sign_texts_read[obj.object_id] = text
            if not text or text == already:
                continue
            if obj.sign_author == self.entity_id:
                continue
            digest.sign_notes.append(sign_note_line(obj))

    def signs_known(self) -> list[ObjectInfo]:
        """Every sign this actor knows about, nearest first."""
        return self.objects_by_type([items.SIGN])

    @staticmethod
    def _note_tick(note: Mapping[str, object]) -> int:
        """A note's `tick` field, defensively parsed; 0 for anything odd."""
        raw = note.get("tick", 0)
        if isinstance(raw, bool):
            return 0
        if isinstance(raw, int):
            return raw
        if isinstance(raw, str) and raw.isdigit():
            return int(raw)
        return 0

    def _check_boards_in_view(self, digest: TickDigest) -> None:
        """Queue one note for every board note by someone else seen for the
        first time, on the same push-notification path as a sign's text
        (docs/08_building.md, "Signs"; docs/09 section 6).

        This is independent of `read_board`/`board_notes_read`, which only
        drive the `look` "(new)" marker: a note announces itself just by
        coming into view, whether or not it has been read.
        """
        for obj in self.objects.values():
            if obj.object_type != items.MESSAGE_BOARD or obj.last_seen != self.tick:
                continue
            notified = self._board_notes_notified.setdefault(obj.object_id, {})
            for slot, note in obj.notes_by_slot().items():
                title = str(note.get("title", ""))
                author = str(note.get("author", ""))
                if not title or author == self.entity_id:
                    continue
                tick = self._note_tick(note)
                if notified.get(slot) == tick:
                    continue
                notified[slot] = tick
                digest.board_notes.append(
                    f"[{obj.object_id}: new note by {author}: {title!r}]"
                )

    def mark_board_read(self, board_id: str) -> None:
        """Record every current note on `board_id` as read by this actor."""
        board = self.objects.get(board_id)
        if board is None:
            return
        read = self.board_notes_read.setdefault(board_id, {})
        for slot, note in board.notes_by_slot().items():
            if str(note.get("title", "")):
                read[slot] = self._note_tick(note)

    def unread_note_count(self, board_id: str) -> int:
        """How many of `board_id`'s current notes this actor has not read."""
        board = self.objects.get(board_id)
        if board is None:
            return 0
        read = self.board_notes_read.get(board_id, {})
        return sum(
            1
            for slot, note in board.notes_by_slot().items()
            if str(note.get("title", "")) and read.get(slot) != self._note_tick(note)
        )

    def is_note_unread(
        self, board_id: str, slot: int, note: Mapping[str, object]
    ) -> bool:
        """Whether this actor has not read this exact version of a note."""
        read = self.board_notes_read.get(board_id, {})
        return read.get(slot) != self._note_tick(note)

    def boards_known(self) -> list[ObjectInfo]:
        """Every message board this actor knows about, nearest first."""
        return self.objects_by_type([items.MESSAGE_BOARD])

    # -- queries ------------------------------------------------------------

    @property
    def position(self) -> Coord:
        """Where this actor is."""
        return self.self_info.position

    def settlement_offset(self) -> Coord:
        """(dx, dy) from this actor to the settlement centre."""
        return (
            self.settlement[0] - self.position[0],
            self.settlement[1] - self.position[1],
        )

    def objects_by_type(
        self, types: Iterable[str], *, origin: Coord | None = None
    ) -> list[ObjectInfo]:
        """Known objects of the given types, nearest first."""
        wanted = frozenset(types)
        centre = self.position if origin is None else origin
        matches = [obj for obj in self.objects.values() if obj.object_type in wanted]
        matches.sort(key=lambda obj: (chebyshev(obj.position, centre), obj.object_id))
        return matches

    def object_at(self, position: Coord) -> list[ObjectInfo]:
        """Every known object standing on `position`."""
        return [obj for obj in self.objects.values() if obj.position == position]

    def ground_objects_at(self, position: Coord) -> list[ObjectInfo]:
        """Known ground-layer objects (road, floors) on `position`."""
        return [
            obj
            for obj in self.object_at(position)
            if obj.object_type in items.GROUND_LAYER_KINDS
        ]

    def structure_objects_at(self, position: Coord) -> list[ObjectInfo]:
        """Known structure-layer objects (everything that is not ground) on `position`."""
        return [
            obj
            for obj in self.object_at(position)
            if obj.object_type not in items.GROUND_LAYER_KINDS
        ]

    def station_near(self, station: str) -> ObjectInfo | None:
        """A placed station of this type on or next to the actor, if it knows of one.

        A station recipe fails anywhere else, so this is the gate for offering
        it at all. An empty `station` (a hand recipe) has no gate and yields
        None, which callers read as "no station needed".
        """
        if not station:
            return None
        for obj in self.objects_near(1):
            if obj.object_type == station:
                return obj
        return None

    def workshop_table_near(self) -> ObjectInfo | None:
        """A placed workshop table on or next to the actor, if it knows of one."""
        return self.station_near(items.WORKSHOP_TABLE)

    def objects_near(self, radius: int) -> list[ObjectInfo]:
        """Known objects within `radius` of the actor, nearest first."""
        centre = self.position
        matches = [
            obj
            for obj in self.objects.values()
            if chebyshev(obj.position, centre) <= radius
        ]
        matches.sort(key=lambda obj: (chebyshev(obj.position, centre), obj.object_id))
        return matches

    def entities_near(self, radius: int) -> list[EntityInfo]:
        """Other entities within `radius` of the actor, nearest first."""
        centre = self.position
        matches = [
            entity
            for entity in self.entities.values()
            if entity.entity_id != self.entity_id
            and chebyshev(entity.position, centre) <= radius
        ]
        matches.sort(
            key=lambda entity: (chebyshev(entity.position, centre), entity.entity_id)
        )
        return matches

    def nearest_wolf(self) -> EntityInfo | None:
        """The closest living wolf the actor knows about, if any."""
        wolves = [
            entity
            for entity in self.entities.values()
            if entity.entity_type == "wolf" and entity.alive
        ]
        if not wolves:
            return None
        return min(wolves, key=lambda w: chebyshev(w.position, self.position))

    def wolves_near(self, radius: int) -> list[EntityInfo]:
        """Living wolves within `radius` of the actor, nearest first."""
        return [
            entity
            for entity in self.entities_near(radius)
            if entity.entity_type == "wolf" and entity.alive
        ]

    def allies_near(self, centre: Coord, radius: int) -> list[EntityInfo]:
        """Other living settlers within `radius` of `centre`."""
        return [
            entity
            for entity in self.entities.values()
            if entity.entity_id != self.entity_id
            and entity.entity_type != "wolf"
            and entity.alive
            and chebyshev(entity.position, centre) <= radius
        ]

    def _remember_conversation_line(self, heard: HeardUtterance) -> None:
        """Append a heard conversation line, ignoring a repeat of the last one."""
        lines = self.conversation_lines.setdefault(heard.conversation_id, [])
        line = TranscriptLine(heard.tick, heard.speaker_id, heard.text)
        if lines and lines[-1] == line:
            return
        lines.append(line)

    def conversations(self) -> list[ConversationInfo]:
        """Every conversation object the actor knows about, nearest first."""
        return [
            conversation_from_object(obj)
            for obj in self.objects_by_type([items.CONVERSATION])
        ]

    def conversation_by_id(self, conversation_id: str) -> ConversationInfo | None:
        """The named conversation, or None when the actor cannot see it."""
        obj = self.objects.get(conversation_id)
        if obj is None or obj.object_type != items.CONVERSATION:
            return None
        return conversation_from_object(obj)

    def my_conversation(self) -> ConversationInfo | None:
        """The conversation this actor currently holds a seat in, if any."""
        for conversation in self.conversations():
            if conversation.has(self.entity_id):
                return conversation
        return None

    def heard_conversation_lines(self, conversation_id: str) -> list[TranscriptLine]:
        """Every line of `conversation_id` this actor heard, oldest first."""
        return list(self.conversation_lines.get(conversation_id, ()))

    def recent_shouts(self, max_age: int) -> list[HeardUtterance]:
        """The latest shout from each other settler in the last `max_age` ticks.

        Newest first. A shout is a call across the map, usually for help.
        """
        latest: dict[str, HeardUtterance] = {}
        for utterance in self.heard:
            if utterance.channel != items.SHOUT_CHANNEL:
                continue
            if utterance.speaker_id == self.entity_id:
                continue
            if self.tick - utterance.tick > max_age:
                continue
            latest[utterance.speaker_id] = utterance
        return sorted(latest.values(), key=lambda u: -u.tick)

    def last_own_shout_tick(self) -> int:
        """The tick of this actor's most recent shout, or -1 when it never has."""
        ticks = [
            u.tick
            for u in self.heard
            if u.speaker_id == self.entity_id and u.channel == items.SHOUT_CHANNEL
        ]
        return max(ticks, default=-1)

    def damage_since(self, tick: int) -> list[DamageTaken]:
        """Every hit the actor took at or after `tick`, oldest first."""
        return [hit for hit in self.damage_log if hit.tick >= tick]

    def recent_history(self, count: int = 8) -> list[str]:
        """The last `count` lines of the actor's own action log."""
        entries = list(self.history)[-count:]
        return [entry.text for entry in entries]

    def recent_utterances(self, count: int = 10) -> list[HeardUtterance]:
        """The last `count` things the actor heard."""
        return list(self.heard)[-count:]


def _entity_info(entity: pb.Entity, tick: int) -> EntityInfo:
    return EntityInfo(
        entity_id=entity.entity_id,
        entity_type=entity.entity_type or "player",
        position=(entity.position.x, entity.position.y),
        health=entity.health,
        max_health=entity.max_health,
        food=entity.food,
        max_food=entity.max_food,
        wielded=entity.wielded,
        alive=entity.alive,
        inventory=inventory_to_dict(entity.inventory),
        last_seen=tick,
        fatigue=entity.fatigue,
        max_fatigue=entity.max_fatigue or items.MAX_FATIGUE,
        asleep=entity.asleep,
        open_to_talk=entity.open_to_talk,
    )


def _world_clock(clock: pb.WorldClock) -> WorldClock:
    """The observation's clock; a world that sends none leaves the defaults."""
    if clock.day_length <= 0:
        return WorldClock()
    return WorldClock(
        day=clock.day,
        tick_of_day=clock.tick_of_day,
        day_length=clock.day_length,
        night=clock.night,
    )


def _delta_name(delta: Coord) -> str:
    from .geometry import DELTA_TO_DIRECTION

    direction = DELTA_TO_DIRECTION.get(delta)
    if direction is None:
        return "?"
    return direction_name(direction)
