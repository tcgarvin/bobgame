"""Agent-side memory of everything this actor has ever observed.

The world server only tells an agent about a 17x17 window each tick. The
`WorldModel` accumulates those windows so the planner and the pathfinder can
reason about places the actor cannot currently see, while still forgetting
things that demonstrably went away (an object inside the current view that is
no longer reported has been removed).

Inside the package: `types` (the frozen value types and their parsers),
`notices` (sign and board read-tracking), `events` (the deaths this actor
witnessed), `payload` (the snapshot codec) and `model` (the class itself).
"""

from __future__ import annotations

from .model import WorldModel
from .types import (
    BLOCKING_OBJECT_TYPES,
    DAMAGE_LOG_LIMIT,
    DEATHS_SEEN_LIMIT,
    DEATHS_SHOWN,
    DEFAULT_REMAINING,
    EXTRACTABLE_TYPES,
    HISTORY_LIMIT,
    ROCK_TYPES,
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
    inventory_to_dict,
    sign_note_line,
)

__all__ = [
    "BLOCKING_OBJECT_TYPES",
    "ConversationInfo",
    "DAMAGE_LOG_LIMIT",
    "DEATHS_SEEN_LIMIT",
    "DEATHS_SHOWN",
    "DEFAULT_REMAINING",
    "DamageTaken",
    "DeathSeen",
    "EXTRACTABLE_TYPES",
    "EntityInfo",
    "HISTORY_LIMIT",
    "HeardUtterance",
    "HistoryEntry",
    "ObjectInfo",
    "ROCK_TYPES",
    "TickDigest",
    "TileInfo",
    "TranscriptLine",
    "UTTERANCE_LIMIT",
    "VIEW_RADIUS",
    "WorldClock",
    "WorldModel",
    "conversation_from_object",
    "inventory_to_dict",
    "sign_note_line",
]
