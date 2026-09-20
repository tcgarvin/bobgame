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
from typing import Any, Iterable, Mapping, Sequence

from .. import world_pb2 as pb
from . import items
from .geometry import DELTA_TO_DIRECTION, Coord, chebyshev, direction_name
from .outcomes import CraftProgress, parse_craft_progress

VIEW_RADIUS = items.VIEW_RADIUS

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
# How many observed deaths the model remembers, and how many `look` shows.
DEATHS_SEEN_LIMIT = 20
DEATHS_SHOWN = 4


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
    day_length: int = items.DEFAULT_DAY_LENGTH_TICKS
    night: bool = False
    # docs/14_new_moon_and_saves.md: whether tonight is a new-moon night, the
    # 0-based day the next one falls on (-1 when the world has none), and the
    # tick a save is being taken on (0 on every other tick).
    new_moon_tonight: bool = False
    next_new_moon_day: int = -1
    save_tick: int = 0

    def as_text(self) -> str:
        """`"day 2 212/300 night"`, the form every tick line uses."""
        return (
            f"day {self.day} {self.tick_of_day}/{self.day_length} "
            f"{'night' if self.night else 'day'}"
        )

    def moon_text(self) -> str:
        """What the clock says about the new moon, or `""` when it says nothing.

        `"new moon tonight (everyone falls asleep at tick-of-day 200)"` on the
        day itself, `"next new moon: night of day 5"` otherwise, and nothing at
        all in a world that has no new moon.
        """
        if self.new_moon_tonight:
            start = items.night_start_tick(self.day_length)
            return f"new moon tonight (everyone falls asleep at tick-of-day {start})"
        if self.next_new_moon_day >= 0:
            return f"next new moon: night of day {self.next_new_moon_day}"
        return ""


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

    def craft_progress(self, entity_id: str) -> CraftProgress:
        """What this crafter has banked at this station, recipe and actions.

        Empty recipe and zero actions when the station holds no progress for
        them, or when the state value is not the `"<recipe>:<done>"` the world
        writes.
        """
        return parse_craft_progress(
            self.state.get(f"{items.CRAFT_PROGRESS_PREFIX}{entity_id}", "")
        )

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
    max_fatigue: int = items.PLAYER_MAX_FATIGUE
    asleep: bool = False
    # Object id of the bed slept on, "" for the ground (and while awake).
    sleeping_on: str = ""
    # True while the sleep was forced by exhaustion rather than chosen.
    collapsed: bool = False

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
    local channel.
    """

    tick: int
    speaker_id: str
    channel: str
    text: str
    position: Coord
    conversation_id: str = ""


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
class DeathSeen:
    """An entity this actor watched die. `killer_id` is "" for starvation.

    Wolf ids are never reused (`world/wolves.py` counts up and skips any id
    still in the world), so a wolf id in here is dead for good. A settler id
    is not: settlers respawn, and `WorldModel.death_of` forgets one as soon as
    the body is seen alive again.
    """

    entity_id: str
    entity_type: str
    tick: int
    killer_id: str

    def fact(self) -> str:
        """`"wolf_6 died at tick 953 (killed by esme)"`."""
        by = f" (killed by {self.killer_id})" if self.killer_id else ""
        return f"{self.entity_id} died at tick {self.tick}{by}"


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
    # True when the world re-delivered a tick this model had already folded in
    # (docs/14 section 4): nothing in here happened now, it happened before the
    # snapshot this agent was restored from.
    repeated: bool = False


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
        # Every death this actor observed, newest last. Hunting a wolf that
        # died 1000 ticks ago cost three settlers most of a day in the
        # 2026-09-20 hamlet run, so the model remembers.
        self.deaths_seen: deque[DeathSeen] = deque(maxlen=DEATHS_SEEN_LIMIT)
        # entity id -> its type, never pruned: an entity is dropped from
        # `entities` the moment it leaves view, and a death event names only
        # the id, so the type has to be remembered separately.
        self._entity_types: dict[str, str] = {}
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
        # The tick of the last observation folded in; -1 before the first one.
        # An observation for that same tick is a re-delivery (docs/14 section
        # 4) and is folded as a refresh rather than as a second tick.
        self.last_observed_tick = -1
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
        """Fold one observation into memory and return what changed.

        An observation for the tick already folded in is a re-delivery: the
        world sends tick `T` again to a resumed agent (docs/14 section 4). Its
        events have already been lived through, so they are skipped and the
        digest comes back empty and marked `repeated`; only the picture of the
        world is refreshed.
        """
        repeated = observation.tick_id == self.last_observed_tick
        self.tick = observation.tick_id
        self.last_observed_tick = observation.tick_id
        digest = TickDigest(tick=observation.tick_id, repeated=repeated)

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
        if not repeated:
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
            self._entity_types[entity.entity_id] = entity.entity_type

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
                self._record_death(died, tick)
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

    def nearest_water(self) -> Coord | None:
        """The nearest water tile this actor has ever observed, or None.

        The observation carries a floor type, not whether the water is fresh:
        rivers, lakes and the sea are all `shallow_water` / `deep_water`. The
        caller must word the fact as "water", not "fresh water".
        """
        centre = self.position
        nearest: Coord | None = None
        best = 0
        for position, tile in self.tiles.items():
            if tile.floor_type not in items.WATER_FLOOR_TYPES:
                continue
            distance = chebyshev(position, centre)
            if nearest is None or distance < best:
                nearest, best = position, distance
        return nearest

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

    def _record_death(self, died: pb.EntityDied, tick: int) -> None:
        """Remember a death this actor observed, replacing any earlier one."""
        self._forget_death(died.entity_id)
        self.deaths_seen.append(
            DeathSeen(
                entity_id=died.entity_id,
                entity_type=self._entity_types.get(died.entity_id, ""),
                tick=tick,
                killer_id=died.killer_id,
            )
        )

    def _forget_death(self, entity_id: str) -> None:
        """Drop any remembered death of `entity_id`, for a body seen alive again."""
        remaining = [
            death for death in self.deaths_seen if death.entity_id != entity_id
        ]
        if len(remaining) == len(self.deaths_seen):
            return
        self.deaths_seen.clear()
        self.deaths_seen.extend(remaining)

    def death_of(self, entity_id: str) -> DeathSeen | None:
        """The remembered death of `entity_id`, or None if it may still be alive.

        A settler that has been observed alive since it died has respawned, so
        its death is forgotten; a wolf id is never reused and stays dead.
        """
        # Snapshot: the loop body can call `_forget_death`, which rewrites the
        # deque, and a live deque iterator refuses to be mutated under it.
        for death in reversed(tuple(self.deaths_seen)):
            if death.entity_id != entity_id:
                continue
            seen = self.entities.get(entity_id)
            if seen is not None and seen.alive and seen.last_seen > death.tick:
                self._forget_death(entity_id)
                return None
            return death
        return None

    def recent_deaths(self, limit: int = DEATHS_SHOWN) -> list[DeathSeen]:
        """The last `limit` deaths this actor saw that are still deaths."""
        # `death_of` forgets the death of a body seen alive again, which
        # rewrites `deaths_seen`; iterate a snapshot so that is allowed.
        found = [
            death
            for death in reversed(tuple(self.deaths_seen))
            if self.death_of(death.entity_id) is not None
        ]
        return list(reversed(found[:limit]))

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

    # -- saving and restoring (docs/14 section 3) ---------------------------

    def to_payload(self) -> dict[str, Any]:
        """Everything this model remembers, as JSON-safe data.

        The derived position indexes and `last_digest` are left out: they are
        rebuilt from the rest by `from_payload`. Tiles are the bulk of a
        settled model (10^5 of them), so they go in parallel arrays with the
        floor types interned rather than as one dict per tile.
        """
        return {
            "entity_id": self.entity_id,
            "tick": self.tick,
            "last_observed_tick": self.last_observed_tick,
            "tiles": _tiles_payload(self.tiles),
            "objects": [_object_payload(obj) for obj in self.objects.values()],
            "entities": [_entity_payload(info) for info in self.entities.values()],
            "self_info": _entity_payload(self.self_info),
            "settlement": list(self.settlement),
            "settlement_known": self.settlement_known,
            "clock": _clock_payload(self.clock),
            "history": [[entry.tick, entry.text] for entry in self.history],
            "heard": [_heard_payload(heard) for heard in self.heard],
            "damage_log": [
                [hit.tick, hit.amount, hit.attacker_id] for hit in self.damage_log
            ],
            "deaths_seen": [
                [death.entity_id, death.entity_type, death.tick, death.killer_id]
                for death in self.deaths_seen
            ],
            "entity_types": dict(self._entity_types),
            "conversation_lines": {
                conversation_id: [
                    [line.tick, line.speaker, line.text] for line in lines
                ]
                for conversation_id, lines in self.conversation_lines.items()
            },
            "sign_texts_read": dict(self.sign_texts_read),
            "board_notes_read": _board_payload(self.board_notes_read),
            "board_notes_notified": _board_payload(self._board_notes_notified),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "WorldModel":
        """Rebuild a model from `to_payload`, derived indexes and all."""
        model = cls(str(payload["entity_id"]))
        model.tick = int(payload["tick"])
        model.last_observed_tick = int(payload["last_observed_tick"])
        model.tiles = _tiles_from_payload(payload["tiles"])
        model.objects = {
            obj.object_id: obj
            for obj in (_object_from_payload(row) for row in payload["objects"])
        }
        model.entities = {
            info.entity_id: info
            for info in (_entity_from_payload(row) for row in payload["entities"])
        }
        model.self_info = _entity_from_payload(payload["self_info"])
        settlement = payload["settlement"]
        model.settlement = (int(settlement[0]), int(settlement[1]))
        model.settlement_known = bool(payload["settlement_known"])
        model.clock = _clock_from_payload(payload["clock"])
        model.history.extend(
            HistoryEntry(int(tick), str(text)) for tick, text in payload["history"]
        )
        model.heard.extend(_heard_from_payload(row) for row in payload["heard"])
        model.damage_log.extend(
            DamageTaken(int(tick), int(amount), str(attacker))
            for tick, amount, attacker in payload["damage_log"]
        )
        model.deaths_seen.extend(
            DeathSeen(str(entity_id), str(entity_type), int(tick), str(killer))
            for entity_id, entity_type, tick, killer in payload["deaths_seen"]
        )
        model._entity_types = {
            str(key): str(value) for key, value in payload["entity_types"].items()
        }
        model.conversation_lines = {
            str(conversation_id): [
                TranscriptLine(int(tick), str(speaker), str(text))
                for tick, speaker, text in lines
            ]
            for conversation_id, lines in payload["conversation_lines"].items()
        }
        model.sign_texts_read = {
            str(key): str(value) for key, value in payload["sign_texts_read"].items()
        }
        model.board_notes_read = _board_from_payload(payload["board_notes_read"])
        model._board_notes_notified = _board_from_payload(
            payload["board_notes_notified"]
        )
        model._rebuild_position_indexes()
        return model


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
        max_fatigue=entity.max_fatigue or items.PLAYER_MAX_FATIGUE,
        asleep=entity.asleep,
        sleeping_on=entity.sleeping_on,
        collapsed=entity.collapsed,
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
        new_moon_tonight=clock.new_moon_tonight,
        next_new_moon_day=clock.next_new_moon_day,
        save_tick=clock.save_tick,
    )


# --- payload helpers (docs/14 "Agent snapshot contents") --------------------

# Bit positions packed into one integer per tile, so a 10^5-tile model does not
# spend two JSON booleans on every one of them.
_TILE_WALKABLE_BIT = 1
_TILE_OPAQUE_BIT = 2


def _tiles_payload(tiles: Mapping[Coord, TileInfo]) -> dict[str, Any]:
    """Tiles as parallel arrays with the floor types interned."""
    floors: list[str] = []
    floor_index: dict[str, int] = {}
    xs: list[int] = []
    ys: list[int] = []
    floor_of: list[int] = []
    flags: list[int] = []
    last_seen: list[int] = []
    for (x, y), tile in tiles.items():
        index = floor_index.get(tile.floor_type, -1)
        if index < 0:
            index = len(floors)
            floor_index[tile.floor_type] = index
            floors.append(tile.floor_type)
        xs.append(x)
        ys.append(y)
        floor_of.append(index)
        flags.append(
            (_TILE_WALKABLE_BIT if tile.walkable else 0)
            | (_TILE_OPAQUE_BIT if tile.opaque else 0)
        )
        last_seen.append(tile.last_seen)
    return {
        "floors": floors,
        "floor_of": floor_of,
        "x": xs,
        "y": ys,
        "flags": flags,
        "last_seen": last_seen,
    }


def _tiles_from_payload(payload: Mapping[str, Any]) -> dict[Coord, TileInfo]:
    """The inverse of `_tiles_payload`."""
    floors = [str(name) for name in payload["floors"]]
    tiles: dict[Coord, TileInfo] = {}
    for x, y, floor, flag, seen in zip(
        payload["x"],
        payload["y"],
        payload["floor_of"],
        payload["flags"],
        payload["last_seen"],
    ):
        position = (int(x), int(y))
        tiles[position] = TileInfo(
            position=position,
            walkable=bool(int(flag) & _TILE_WALKABLE_BIT),
            opaque=bool(int(flag) & _TILE_OPAQUE_BIT),
            floor_type=floors[int(floor)],
            last_seen=int(seen),
        )
    return tiles


def _object_payload(obj: ObjectInfo) -> dict[str, Any]:
    return {
        "object_id": obj.object_id,
        "object_type": obj.object_type,
        "position": list(obj.position),
        "state": dict(obj.state),
        "last_seen": obj.last_seen,
    }


def _object_from_payload(payload: Mapping[str, Any]) -> ObjectInfo:
    position = payload["position"]
    return ObjectInfo(
        object_id=str(payload["object_id"]),
        object_type=str(payload["object_type"]),
        position=(int(position[0]), int(position[1])),
        state={str(key): str(value) for key, value in payload["state"].items()},
        last_seen=int(payload["last_seen"]),
    )


def _entity_payload(info: EntityInfo) -> dict[str, Any]:
    return {
        "entity_id": info.entity_id,
        "entity_type": info.entity_type,
        "position": list(info.position),
        "health": info.health,
        "max_health": info.max_health,
        "food": info.food,
        "max_food": info.max_food,
        "wielded": info.wielded,
        "alive": info.alive,
        "inventory": dict(info.inventory),
        "last_seen": info.last_seen,
        "fatigue": info.fatigue,
        "max_fatigue": info.max_fatigue,
        "asleep": info.asleep,
        "sleeping_on": info.sleeping_on,
        "collapsed": info.collapsed,
    }


def _entity_from_payload(payload: Mapping[str, Any]) -> EntityInfo:
    position = payload["position"]
    return EntityInfo(
        entity_id=str(payload["entity_id"]),
        entity_type=str(payload["entity_type"]),
        position=(int(position[0]), int(position[1])),
        health=int(payload["health"]),
        max_health=int(payload["max_health"]),
        food=int(payload["food"]),
        max_food=int(payload["max_food"]),
        wielded=str(payload["wielded"]),
        alive=bool(payload["alive"]),
        inventory={
            str(kind): int(count) for kind, count in payload["inventory"].items()
        },
        last_seen=int(payload["last_seen"]),
        fatigue=int(payload["fatigue"]),
        max_fatigue=int(payload["max_fatigue"]),
        asleep=bool(payload["asleep"]),
        sleeping_on=str(payload["sleeping_on"]),
        collapsed=bool(payload["collapsed"]),
    )


def _clock_payload(clock: WorldClock) -> dict[str, Any]:
    return {
        "day": clock.day,
        "tick_of_day": clock.tick_of_day,
        "day_length": clock.day_length,
        "night": clock.night,
        "new_moon_tonight": clock.new_moon_tonight,
        "next_new_moon_day": clock.next_new_moon_day,
        # Deliberately not carried: the save tick belongs to the tick the
        # snapshot was taken on, and the resumed world re-delivers it as 0.
    }


def _clock_from_payload(payload: Mapping[str, Any]) -> WorldClock:
    return WorldClock(
        day=int(payload["day"]),
        tick_of_day=int(payload["tick_of_day"]),
        day_length=int(payload["day_length"]),
        night=bool(payload["night"]),
        new_moon_tonight=bool(payload["new_moon_tonight"]),
        next_new_moon_day=int(payload["next_new_moon_day"]),
    )


def _heard_payload(heard: HeardUtterance) -> list[Any]:
    return [
        heard.tick,
        heard.speaker_id,
        heard.channel,
        heard.text,
        list(heard.position),
        heard.conversation_id,
    ]


def _heard_from_payload(row: Sequence[Any]) -> HeardUtterance:
    tick, speaker, channel, text, position, conversation_id = row
    return HeardUtterance(
        tick=int(tick),
        speaker_id=str(speaker),
        channel=str(channel),
        text=str(text),
        position=(int(position[0]), int(position[1])),
        conversation_id=str(conversation_id),
    )


def _board_payload(boards: Mapping[str, Mapping[int, int]]) -> dict[str, Any]:
    """Board read-tracking, with the integer slots as JSON's string keys."""
    return {
        board_id: {str(slot): tick for slot, tick in slots.items()}
        for board_id, slots in boards.items()
    }


def _board_from_payload(payload: Mapping[str, Any]) -> dict[str, dict[int, int]]:
    return {
        str(board_id): {int(slot): int(tick) for slot, tick in slots.items()}
        for board_id, slots in payload.items()
    }


def _delta_name(delta: Coord) -> str:
    direction = DELTA_TO_DIRECTION.get(delta)
    if direction is None:
        return "?"
    return direction_name(direction)
