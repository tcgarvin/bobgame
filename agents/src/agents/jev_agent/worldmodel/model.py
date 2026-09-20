"""Agent-side memory of everything this actor has ever observed.

The world server only tells an agent about a 17x17 window each tick. The
`WorldModel` accumulates those windows so the planner and the pathfinder can
reason about places the actor cannot currently see, while still forgetting
things that demonstrably went away (an object inside the current view that is
no longer reported has been removed).

It is one class on purpose: every method reads the same handful of indexes, so
what is extractable is the bookkeeping that owns state of its own - the sign
and board read-tracking in `notices.py`, the death memory in `events.py` and
the snapshot codec in `payload.py`.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Iterable, Mapping

from ... import world_pb2 as pb
from .. import items
from ..geometry import Coord, chebyshev
from . import events, notices
from .payload import (
    board_from_payload,
    board_payload,
    clock_from_payload,
    clock_payload,
    entity_from_payload,
    entity_payload,
    heard_from_payload,
    heard_payload,
    object_from_payload,
    object_payload,
    tiles_from_payload,
    tiles_payload,
)
from .types import (
    BLOCKING_OBJECT_TYPES,
    DAMAGE_LOG_LIMIT,
    DEATHS_SEEN_LIMIT,
    DEATHS_SHOWN,
    HISTORY_LIMIT,
    UTTERANCE_LIMIT,
    VIEW_RADIUS,
    ConversationInfo,
    DamageTaken,
    DeathSeen,
    EntityInfo,
    HeardUtterance,
    HistoryEntry,
    ObjectInfo,
    TickDigest,
    TileInfo,
    TranscriptLine,
    WorldClock,
    conversation_from_object,
    entity_info,
    world_clock,
)


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

        self.self_info = entity_info(observation.self, observation.tick_id)
        self.clock = world_clock(observation.clock)
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
            self.entities[entity.entity_id] = entity_info(entity, observation.tick_id)
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
                    HistoryEntry(tick, f"t{tick} move {events.delta_name(delta)} ok")
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
                events.record_death(self.deaths_seen, self._entity_types, died, tick)
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
        """Queue a note for every sign in view whose line is new to this actor."""
        digest.sign_notes.extend(
            notices.sign_notes_in_view(
                self.objects.values(), self.tick, self.entity_id, self.sign_texts_read
            )
        )

    def signs_known(self) -> list[ObjectInfo]:
        """Every sign this actor knows about, nearest first."""
        return self.objects_by_type([items.SIGN])

    def _check_boards_in_view(self, digest: TickDigest) -> None:
        """Queue a note for every board note by someone else seen for the first time."""
        digest.board_notes.extend(
            notices.board_notes_in_view(
                self.objects.values(),
                self.tick,
                self.entity_id,
                self._board_notes_notified,
            )
        )

    def mark_board_read(self, board_id: str) -> None:
        """Record every current note on `board_id` as read by this actor."""
        board = self.objects.get(board_id)
        if board is None:
            return
        notices.mark_board_read(board, self.board_notes_read.setdefault(board_id, {}))

    def unread_note_count(self, board_id: str) -> int:
        """How many of `board_id`'s current notes this actor has not read."""
        board = self.objects.get(board_id)
        if board is None:
            return 0
        return notices.unread_note_count(board, self.board_notes_read.get(board_id, {}))

    def is_note_unread(
        self, board_id: str, slot: int, note: Mapping[str, object]
    ) -> bool:
        """Whether this actor has not read this exact version of a note."""
        return notices.is_note_unread(
            self.board_notes_read.get(board_id, {}), slot, note
        )

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

    def death_of(self, entity_id: str) -> DeathSeen | None:
        """The remembered death of `entity_id`, or None if it may still be alive.

        A settler that has been observed alive since it died has respawned, so
        its death is forgotten; a wolf id is never reused and stays dead.
        """
        return events.death_of(self.deaths_seen, self.entities, entity_id)

    def recent_deaths(self, limit: int = DEATHS_SHOWN) -> list[DeathSeen]:
        """The last `limit` deaths this actor saw that are still deaths."""
        return events.recent_deaths(self.deaths_seen, self.entities, limit)

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
            "tiles": tiles_payload(self.tiles),
            "objects": [object_payload(obj) for obj in self.objects.values()],
            "entities": [entity_payload(info) for info in self.entities.values()],
            "self_info": entity_payload(self.self_info),
            "settlement": list(self.settlement),
            "settlement_known": self.settlement_known,
            "clock": clock_payload(self.clock),
            "history": [[entry.tick, entry.text] for entry in self.history],
            "heard": [heard_payload(heard) for heard in self.heard],
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
            "board_notes_read": board_payload(self.board_notes_read),
            "board_notes_notified": board_payload(self._board_notes_notified),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "WorldModel":
        """Rebuild a model from `to_payload`, derived indexes and all."""
        model = cls(str(payload["entity_id"]))
        model.tick = int(payload["tick"])
        model.last_observed_tick = int(payload["last_observed_tick"])
        model.tiles = tiles_from_payload(payload["tiles"])
        model.objects = {
            obj.object_id: obj
            for obj in (object_from_payload(row) for row in payload["objects"])
        }
        model.entities = {
            info.entity_id: info
            for info in (entity_from_payload(row) for row in payload["entities"])
        }
        model.self_info = entity_from_payload(payload["self_info"])
        settlement = payload["settlement"]
        model.settlement = (int(settlement[0]), int(settlement[1]))
        model.settlement_known = bool(payload["settlement_known"])
        model.clock = clock_from_payload(payload["clock"])
        model.history.extend(
            HistoryEntry(int(tick), str(text)) for tick, text in payload["history"]
        )
        model.heard.extend(heard_from_payload(row) for row in payload["heard"])
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
        model.board_notes_read = board_from_payload(payload["board_notes_read"])
        model._board_notes_notified = board_from_payload(
            payload["board_notes_notified"]
        )
        model._rebuild_position_indexes()
        return model
