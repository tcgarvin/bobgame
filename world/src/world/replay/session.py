"""A seekable world reconstructed from a recorded run."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from ..chunks import CHUNK_SIZE, ChunkManager
from ..exceptions import ObjectNotFoundError, PositionOccupiedError
from ..state import World, WorldObject
from ..terrain.persistence import load_map
from ..types import Position
from ..viewer_payload import clock_payload, entity_from_state, object_from_state
from .loader import RunLoader

logger = structlog.get_logger()

# How far back `entity_log` looks, and how many entries it keeps per entity.
LOG_LOOKBACK_TICKS = 50
LOG_ENTRIES_PER_ENTITY = 10


class ReplaySession:
    """One run positioned at one tick, with a World the viewer can be fed from.

    Seeking forward applies object deltas; seeking backward restores the
    objects touched since the baseline and replays from the first tick, so a
    rollback never rebuilds every object on the island.
    """

    def __init__(self, loader: RunLoader, project_root: Path):
        self.loader = loader
        self.project_root = project_root
        self.world = _build_world(loader, project_root)
        self.chunk_manager = ChunkManager(self.world)
        self.chunk_manager.initialize_from_world()

        # Copy-on-write baseline: an object's tick-0 value is kept the first
        # time a delta touches it, so a rollback never rebuilds every object.
        self._baseline: dict[str, WorldObject] = {}
        # Objects that did not exist at the baseline and must go on rollback.
        self._added: set[str] = set()
        self._applied_through = loader.first_tick - 1

        self.playing = False
        self.speed = 1.0
        self.tick_id = loader.first_tick
        self.seek(loader.first_tick)

    # --- position ---

    @property
    def first_tick(self) -> int:
        return self.loader.first_tick

    @property
    def last_tick(self) -> int:
        return self.loader.last_tick

    def clamp(self, tick_id: int) -> int:
        """Clamp a tick to the run's range."""
        return max(self.first_tick, min(self.last_tick, tick_id))

    def seek(self, tick_id: int) -> int:
        """Move to `tick_id` (clamped) and return where we landed."""
        target = self.clamp(tick_id)
        if target < self._applied_through:
            self._rollback_objects()
            self._applied_through = self.first_tick - 1

        for record in self.loader.ticks_in_range(self._applied_through + 1, target):
            self._apply_objects(record)
        self._applied_through = target

        self.world.tick = target
        self._apply_entities(self.loader.tick_record(target))
        self.tick_id = target
        return target

    def step(self, delta: int) -> int:
        """Seek by `delta` ticks from the current position."""
        return self.seek(self.tick_id + delta)

    def at_end(self) -> bool:
        """Whether the session sits on the last recorded tick."""
        return self.tick_id >= self.last_tick

    # --- payloads ---

    def replay_status(self) -> dict[str, Any]:
        """The `replay_status` message for the current state."""
        return {
            "type": "replay_status",
            "tick_id": self.tick_id,
            "playing": self.playing,
            "speed": self.speed,
            "first_tick": self.first_tick,
            "last_tick": self.last_tick,
        }

    def snapshot(self) -> dict[str, Any]:
        """The live `snapshot` message plus the replay block."""
        settlement = self.world.settlement
        return {
            "type": "snapshot",
            "tick_id": self.tick_id,
            "clock": clock_payload(self.world.clock),
            "world_size": {"width": self.world.width, "height": self.world.height},
            "chunk_size": CHUNK_SIZE,
            "tick_duration_ms": self.loader.tick_duration_ms,
            "settlement": (
                {"x": settlement.x, "y": settlement.y}
                if settlement is not None
                else None
            ),
            "run_id": self.loader.run_id,
            "replay": {
                "run_id": self.loader.run_id,
                "first_tick": self.first_tick,
                "last_tick": self.last_tick,
                "tick_id": self.tick_id,
                "playing": self.playing,
                "speed": self.speed,
            },
        }

    def tick_completed(self, tick_id: int) -> dict[str, Any]:
        """The `tick_completed` message for a recorded tick."""
        record = self.loader.tick_record(tick_id)
        return {
            "type": "tick_completed",
            "tick_id": tick_id,
            "clock": record.get("clock") or clock_payload(self.world.clock),
            "moves": record.get("moves", []),
            "object_changes": record.get("object_changes", []),
            "actions_processed": len(record.get("moves", []))
            + len(record.get("actions", [])),
            "entity_updates": record.get("entity_updates", []),
            "actions": record.get("actions", []),
            "utterances": record.get("utterances", []),
            "objects_added": record.get("objects_added", []),
            "objects_removed": record.get("objects_removed", []),
        }

    def tick_started(self, tick_id: int) -> dict[str, Any]:
        """The `tick_started` message, with the duration scaled by `speed`."""
        record = self.loader.tick_record(tick_id)
        wall_ms = int(record.get("wall_ms", 0))
        duration = int(self.loader.tick_duration_ms / self.speed)
        return {
            "type": "tick_started",
            "tick_id": tick_id,
            "tick_start_ms": wall_ms,
            "deadline_ms": wall_ms + duration // 2,
            "tick_duration_ms": duration,
        }

    def spawn_messages(self, tick_id: int) -> list[dict[str, Any]]:
        """`entity_spawned` / `entity_despawned` messages for a recorded tick."""
        record = self.loader.tick_record(tick_id)
        states = {
            update["entity_id"]: update for update in record.get("entity_updates", ())
        }
        messages: list[dict[str, Any]] = []
        for spawned in record.get("entities_spawned", ()):
            entity_id = spawned.get("entity_id", "")
            messages.append(
                {
                    "type": "entity_spawned",
                    "tick_id": tick_id,
                    "entity": states.get(entity_id, _fallback_entity(spawned)),
                }
            )
        for respawned in record.get("respawns", ()):
            entity_id = respawned.get("entity_id", "")
            messages.append(
                {
                    "type": "entity_spawned",
                    "tick_id": tick_id,
                    "entity": states.get(
                        entity_id,
                        _fallback_entity({**respawned, "entity_type": "player"}),
                    ),
                }
            )
        for despawned in record.get("entities_despawned", ()):
            messages.append(
                {
                    "type": "entity_despawned",
                    "tick_id": tick_id,
                    "entity_id": despawned.get("entity_id", ""),
                    "reason": despawned.get("reason", ""),
                }
            )
        return messages

    def entity_log(self, tick_id: int) -> dict[str, Any]:
        """Recent actions and utterances per entity before `tick_id`."""
        start = max(self.first_tick, tick_id - LOG_LOOKBACK_TICKS + 1)
        per_entity: dict[str, list[dict[str, Any]]] = {}
        for record in self.loader.ticks_in_range(start, tick_id):
            record_tick = int(record["tick_id"])
            for action in record.get("actions", ()):
                entity_id = action.get("entity_id", "")
                per_entity.setdefault(entity_id, []).append(
                    {
                        "tick_id": record_tick,
                        "entity_id": entity_id,
                        "kind": "action",
                        "text": _action_text(action),
                        "success": bool(action.get("success", False)),
                        "channel": "",
                    }
                )
            for utterance in record.get("utterances", ()):
                entity_id = utterance.get("speaker_id", "")
                per_entity.setdefault(entity_id, []).append(
                    {
                        "tick_id": record_tick,
                        "entity_id": entity_id,
                        "kind": "utterance",
                        "text": utterance.get("text", ""),
                        "success": True,
                        "channel": utterance.get("channel", ""),
                    }
                )
        entries: list[dict[str, Any]] = []
        for entity_entries in per_entity.values():
            entries.extend(entity_entries[-LOG_ENTRIES_PER_ENTITY:])
        entries.sort(key=lambda entry: (entry["tick_id"], entry["entity_id"]))
        return {"type": "entity_log", "tick_id": tick_id, "entries": entries}

    def agent_status_messages(self, tick_id: int) -> list[dict[str, Any]]:
        """`agent_status` messages for every entity that has one by `tick_id`."""
        return [
            {
                "type": "agent_status",
                "entity_id": status.get("entity_id", ""),
                "mode": status.get("mode", ""),
                "brief": status.get("brief", ""),
                "planner_thought": status.get("planner_thought", ""),
                "stint": status.get("stint"),
                "cost": status.get("cost"),
            }
            for status in self.loader.agent_status_up_to(tick_id)
        ]

    def agent_status_at(self, tick_id: int) -> list[dict[str, Any]]:
        """`agent_status` messages recorded exactly at `tick_id`."""
        return [
            {
                "type": "agent_status",
                "entity_id": status.get("entity_id", ""),
                "mode": status.get("mode", ""),
                "brief": status.get("brief", ""),
                "planner_thought": status.get("planner_thought", ""),
                "stint": status.get("stint"),
                "cost": status.get("cost"),
            }
            for status in self.loader.agent_status.get(tick_id, [])
        ]

    # --- world reconstruction ---

    def _apply_objects(self, record: dict[str, Any]) -> None:
        """Apply one tick's object deltas to the world and the chunk index."""
        for change in record.get("object_changes", ()):
            object_id = change.get("object_id", "")
            try:
                obj = self.world.get_object(object_id)
            except ObjectNotFoundError:
                continue
            self._remember(obj)
            self.world.update_object(
                obj.with_state(change.get("field", ""), change.get("new_value", ""))
            )

        for added in record.get("objects_added", ()):
            obj = object_from_state(added)
            try:
                existing = self.world.get_object(obj.object_id)
            except ObjectNotFoundError:
                if obj.object_id not in self._baseline:
                    self._added.add(obj.object_id)
                self.world.add_object(obj)
                self.chunk_manager.add_object(obj.object_id, obj.position)
                continue
            self._remember(existing)
            self.world.update_object(obj)

        for object_id in record.get("objects_removed", ()):
            try:
                obj = self.world.get_object(object_id)
            except ObjectNotFoundError:
                continue
            self._remember(obj)
            self.world.remove_object(object_id)
            self.chunk_manager.remove_object(object_id)

    def _remember(self, obj: WorldObject) -> None:
        """Keep an object's baseline value the first time a delta touches it."""
        if obj.object_id not in self._baseline and obj.object_id not in self._added:
            self._baseline[obj.object_id] = obj

    def _rollback_objects(self) -> None:
        """Restore every object touched since the baseline."""
        for object_id, obj in self._baseline.items():
            if object_id in self.world.all_objects():
                self.world.update_object(obj)
            else:
                self.world.add_object(obj)
                self.chunk_manager.add_object(obj.object_id, obj.position)
        for object_id in self._added:
            if object_id in self.world.all_objects():
                self.world.remove_object(object_id)
                self.chunk_manager.remove_object(object_id)
        self._baseline.clear()
        self._added.clear()

    def _apply_entities(self, record: dict[str, Any]) -> None:
        """Replace the entity set with the tick's `entity_updates`.

        Dead entities stay in the world (the viewer draws them) but are
        detached from the position index, as the live world keeps them.
        """
        previous = set(self.world.all_entities())
        for entity_id in previous:
            entity = self.world.get_entity(entity_id)
            placed = self.world.get_entity_at(entity.position)
            if placed is not None and placed.entity_id == entity_id:
                self.world.detach_entity(entity_id)
            self.world.discard_entity(entity_id)

        updates = record.get("entity_updates", [])
        current: set[str] = set()
        # Dead entities first: each is detached immediately, so they never
        # hold a tile against a living entity standing on it.
        for update in sorted(updates, key=lambda item: bool(item.get("alive", True))):
            entity = entity_from_state(update)
            try:
                self.world.add_entity(entity)
            except PositionOccupiedError:
                logger.warning(
                    "replay_entity_position_conflict",
                    entity_id=entity.entity_id,
                    x=entity.position.x,
                    y=entity.position.y,
                )
                continue
            if not entity.alive:
                self.world.detach_entity(entity.entity_id)
            current.add(entity.entity_id)
            self.chunk_manager.sync_entity_position(entity.entity_id, entity.position)

        for entity_id in previous - current:
            self.chunk_manager.remove_entity(entity_id)


def _action_text(action: dict[str, Any]) -> str:
    """One-line text for an action in the entity log."""
    details = action.get("details", "")
    action_type = action.get("action_type", "")
    return f"{action_type}: {details}" if details else action_type


def _fallback_entity(event: dict[str, Any]) -> dict[str, Any]:
    """Entity state for a spawn whose entity is missing from entity_updates."""
    position = event.get("position", {"x": 0, "y": 0})
    return {
        "entity_id": event.get("entity_id", ""),
        "position": position,
        "entity_type": event.get("entity_type", "default"),
        "tags": [],
        "health": 0,
        "max_health": 0,
        "food": 0,
        "max_food": 0,
        "wielded": "",
        "alive": False,
        "fatigue": 0,
        "max_fatigue": 0,
        "asleep": False,
        "sleeping_on": "",
        "collapsed": False,
        "inventory": {},
    }


def _build_world(loader: RunLoader, project_root: Path) -> World:
    """Build the run's world: terrain from the map, objects from the baseline."""
    map_path = loader.map_path
    if map_path:
        map_file = project_root / map_path
        floor, _placed, _metadata = load_map(map_file)
        height, width = floor.shape
        world = World(
            width=int(width),
            height=int(height),
            day_length_ticks=loader.day_length_ticks,
        )
        world.set_floor_array(floor)
        if not loader.map_matches(project_root):
            logger.warning(
                "replay_map_hash_mismatch",
                run_id=loader.run_id,
                path=str(map_file),
            )
    else:
        world = World(
            width=loader.world_width,
            height=loader.world_height,
            day_length_ticks=loader.day_length_ticks,
        )

    for state in loader.iter_objects():
        world.add_object(object_from_state(state))

    settlement = loader.meta.get("settlement")
    if isinstance(settlement, dict):
        world.settlement = Position(x=int(settlement["x"]), y=int(settlement["y"]))
    return world
