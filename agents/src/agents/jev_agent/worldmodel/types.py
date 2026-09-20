"""The frozen value types the world model is made of, and their parsers.

Everything the model remembers is one of these: a tile, an object, an entity, a
line somebody said, a conversation, a death. They carry no behaviour beyond
reading their own `state` strings, which is why every other module in the
package can import them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Mapping

from ... import world_pb2 as pb
from .. import items
from ..geometry import Coord
from ..outcomes import CraftProgress, parse_craft_progress

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


def entity_info(entity: pb.Entity, tick: int) -> EntityInfo:
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


def world_clock(clock: pb.WorldClock) -> WorldClock:
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
