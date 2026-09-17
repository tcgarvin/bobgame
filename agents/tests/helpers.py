"""Builders for synthetic observations and a scripted stand-in for Jev."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from agents import world_pb2 as pb
from agents.jev_agent.conversation import ConverserMove
from agents.jev_agent.jevclient import JevDecision

GRASS = "grass"
WATER = "deep_water"


def make_entity(
    entity_id: str,
    position: tuple[int, int],
    *,
    entity_type: str = "player",
    health: int = 20,
    max_health: int = 20,
    hunger: int = 80,
    max_hunger: int = 100,
    wielded: str = "",
    alive: bool = True,
    inventory: Mapping[str, int] | None = None,
) -> pb.Entity:
    """A proto Entity with sensible player defaults."""
    items = [
        pb.InventoryItem(kind=kind, quantity=quantity)
        for kind, quantity in sorted((inventory or {}).items())
    ]
    return pb.Entity(
        entity_id=entity_id,
        position=pb.Position(x=position[0], y=position[1]),
        entity_type=entity_type,
        health=health,
        max_health=max_health,
        hunger=hunger,
        max_hunger=max_hunger,
        wielded=wielded,
        alive=alive,
        inventory=pb.Inventory(items=items),
    )


def make_object(
    object_id: str,
    object_type: str,
    position: tuple[int, int],
    state: Mapping[str, str] | None = None,
) -> pb.WorldObject:
    """A proto WorldObject."""
    return pb.WorldObject(
        object_id=object_id,
        object_type=object_type,
        position=pb.Position(x=position[0], y=position[1]),
        state=dict(state or {}),
    )


def make_tiles(
    centre: tuple[int, int],
    radius: int = 8,
    *,
    blocked: Iterable[tuple[int, int]] = (),
    floor: str = GRASS,
) -> list[pb.Tile]:
    """A square of walkable tiles around `centre`, minus `blocked`."""
    blocked_set = set(blocked)
    tiles: list[pb.Tile] = []
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            position = (centre[0] + dx, centre[1] + dy)
            is_blocked = position in blocked_set
            tiles.append(
                pb.Tile(
                    position=pb.Position(x=position[0], y=position[1]),
                    walkable=not is_blocked,
                    opaque=is_blocked,
                    floor_type="mountain" if is_blocked else floor,
                )
            )
    return tiles


def make_observation(
    tick: int,
    self_entity: pb.Entity,
    *,
    tiles: Sequence[pb.Tile] | None = None,
    objects: Sequence[pb.WorldObject] = (),
    entities: Sequence[pb.Entity] = (),
    events: Sequence[pb.ObservationEvent] = (),
) -> pb.Observation:
    """An Observation with a fully walkable view unless told otherwise."""
    position = (self_entity.position.x, self_entity.position.y)
    return pb.Observation(
        tick_id=tick,
        self=self_entity,
        visible_tiles=list(tiles if tiles is not None else make_tiles(position)),
        visible_objects=list(objects),
        visible_entities=list(entities),
        events=list(events),
    )


def acted_event(
    entity_id: str, action_type: str, success: bool, details: str = ""
) -> pb.ObservationEvent:
    """An `EntityActed` observation event."""
    return pb.ObservationEvent(
        entity_acted=pb.EntityActed(
            entity_id=entity_id,
            action_type=action_type,
            success=success,
            details=details,
        )
    )


def utterance_event(
    speaker_id: str,
    text: str,
    position: tuple[int, int],
    channel: str = "local",
) -> pb.ObservationEvent:
    """An `Utterance` observation event spoken from `position`."""
    return pb.ObservationEvent(
        utterance=pb.Utterance(
            speaker_id=speaker_id,
            channel=channel,
            text=text,
            position=pb.Position(x=position[0], y=position[1]),
        )
    )


def damaged_event(
    entity_id: str, attacker_id: str, amount: int, remaining_health: int
) -> pb.ObservationEvent:
    """An `EntityDamaged` observation event."""
    return pb.ObservationEvent(
        entity_damaged=pb.EntityDamaged(
            entity_id=entity_id,
            attacker_id=attacker_id,
            amount=amount,
            remaining_health=remaining_health,
        )
    )


@dataclass
class FakeJevClient:
    """A `JevClient` that replays scripted answers and records what it saw."""

    script: list[JevDecision] = field(default_factory=list)
    default_action: str = "wait"
    calls: list[tuple[dict[str, Any], dict[str, str]]] = field(default_factory=list)

    async def decide(
        self, state: Mapping[str, Any], options: Mapping[str, str]
    ) -> JevDecision:
        """Pop the next scripted decision, falling back to `default_action`."""
        self.calls.append((dict(state), dict(options)))
        if self.script:
            return self.script.pop(0)
        return JevDecision(
            action=self.default_action,
            probabilities={self.default_action: 1.0},
            confidence=1.0,
            input_tokens=100,
            latency_ms=1,
        )

    @property
    def last_options(self) -> dict[str, str]:
        """The option set offered on the most recent call."""
        return self.calls[-1][1]

    @property
    def last_state(self) -> dict[str, Any]:
        """The state sent on the most recent call."""
        return self.calls[-1][0]


def converse_object(
    conversation_id: str,
    anchor: tuple[int, int],
    participants: Sequence[str],
    *,
    speaker: str = "",
    turn_started: int = 0,
    utterances: int = 0,
    transcript: Sequence[Mapping[str, Any]] = (),
) -> pb.WorldObject:
    """A `conversation` world object with the state the world writes."""
    return make_object(
        conversation_id,
        "conversation",
        anchor,
        {
            "participants": json.dumps(list(participants)),
            "speaker": speaker,
            "turn_started": str(turn_started),
            "opened_tick": "1",
            "opened_by": participants[0] if participants else "",
            "utterances": str(utterances),
            "transcript": json.dumps(list(transcript)),
        },
    )


def conversation_utterance_event(
    speaker_id: str,
    text: str,
    position: tuple[int, int],
    conversation_id: str,
    channel: str = "conversation",
) -> pb.ObservationEvent:
    """An `Utterance` event that belongs to a conversation."""
    return pb.ObservationEvent(
        utterance=pb.Utterance(
            speaker_id=speaker_id,
            channel=channel,
            text=text,
            position=pb.Position(x=position[0], y=position[1]),
            conversation_id=conversation_id,
        )
    )


@dataclass
class FakeConverser:
    """A `Converser` that replays scripted moves and records its prompts."""

    script: list[ConverserMove] = field(default_factory=list)
    default_action: str = "pass"
    note_text: str = ""
    prompts: list[str] = field(default_factory=list)
    note_prompts: list[str] = field(default_factory=list)

    async def move(self, prompt: str) -> ConverserMove:
        """Pop the next scripted move, falling back to `default_action`."""
        self.prompts.append(prompt)
        if self.script:
            return self.script.pop(0)
        return ConverserMove(action=self.default_action)

    async def note(self, prompt: str) -> str:
        """Return the fixed note text."""
        self.note_prompts.append(prompt)
        return self.note_text
