"""Event records produced during a tick and consumed by observation/viewer services.

These are plain dataclasses (not pydantic) because they are transient per-tick
records, never persisted or validated at a boundary.
"""

from dataclasses import dataclass, field

from .state import WorldObject
from .types import Position


@dataclass(frozen=True)
class ActionResult:
    """Outcome of a non-movement action submitted by an entity."""

    entity_id: str
    action_type: str  # attack, extract, collect, pickup, withdraw, drop, deposit, craft, equip, place, write_note, eat, say, converse, give, wait
    success: bool
    details: str = ""


@dataclass(frozen=True)
class DamageEvent:
    entity_id: str
    attacker_id: str  # "" for starvation
    amount: int
    remaining_health: int
    position: Position


@dataclass(frozen=True)
class DeathEvent:
    entity_id: str
    killer_id: str  # "" for starvation
    position: Position


@dataclass(frozen=True)
class RespawnEvent:
    entity_id: str
    position: Position


@dataclass(frozen=True)
class UtteranceEvent:
    speaker_id: str
    channel: str  # "local" | "shout" | "conversation" | "thought"
    text: str
    position: Position
    # Set for `conversation` lines and for a conversation's opening line.
    conversation_id: str = ""


@dataclass(frozen=True)
class ObjectAddedEvent:
    obj: WorldObject


@dataclass(frozen=True)
class ObjectRemovedEvent:
    object_id: str
    position: Position


@dataclass(frozen=True)
class EntitySpawnedEvent:
    entity_id: str
    position: Position
    entity_type: str


@dataclass(frozen=True)
class EntityDespawnedEvent:
    entity_id: str
    position: Position
    reason: str


@dataclass(frozen=True)
class ObjectChange:
    """Record of an object state change (one field)."""

    object_id: str
    field: str
    old_value: str
    new_value: str


@dataclass
class TickEvents:
    """Mutable accumulator for everything that happened during one tick.

    Phase functions append to this; `TickLoop` copies the lists onto the
    `TickResult` it returns.
    """

    action_results: list[ActionResult] = field(default_factory=list)
    damage_events: list[DamageEvent] = field(default_factory=list)
    deaths: list[DeathEvent] = field(default_factory=list)
    respawns: list[RespawnEvent] = field(default_factory=list)
    utterances: list[UtteranceEvent] = field(default_factory=list)
    objects_added: list[ObjectAddedEvent] = field(default_factory=list)
    objects_removed: list[ObjectRemovedEvent] = field(default_factory=list)
    entities_spawned: list[EntitySpawnedEvent] = field(default_factory=list)
    entities_despawned: list[EntityDespawnedEvent] = field(default_factory=list)
    object_changes: list[ObjectChange] = field(default_factory=list)

    def acted(
        self, entity_id: str, action_type: str, success: bool, details: str = ""
    ) -> None:
        """Record the outcome of one non-movement action."""
        self.action_results.append(
            ActionResult(
                entity_id=entity_id,
                action_type=action_type,
                success=success,
                details=details,
            )
        )
