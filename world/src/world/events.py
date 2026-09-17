"""Event records produced during a tick and consumed by observation/viewer services.

These are plain dataclasses (not pydantic) because they are transient per-tick
records, never persisted or validated at a boundary.
"""

from dataclasses import dataclass

from .state import WorldObject
from .types import Position


@dataclass(frozen=True)
class ActionResult:
    """Outcome of a non-movement action submitted by an entity."""

    entity_id: str
    action_type: str  # attack, extract, collect, pickup, withdraw, drop, deposit, craft, equip, place, write_note, eat, say, wait
    success: bool
    details: str = ""


@dataclass(frozen=True)
class DamageEvent:
    entity_id: str
    attacker_id: str  # "" for hunger/environment
    amount: int
    remaining_health: int
    position: Position


@dataclass(frozen=True)
class DeathEvent:
    entity_id: str
    killer_id: str  # "" for hunger/environment
    position: Position


@dataclass(frozen=True)
class RespawnEvent:
    entity_id: str
    position: Position


@dataclass(frozen=True)
class UtteranceEvent:
    speaker_id: str
    channel: str  # "local" | "thought"
    text: str
    position: Position


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
