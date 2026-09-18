"""JSON payload builders shared by the live viewer service and the recorder.

The viewer WebSocket protocol and the recorded `tick` record use the same
field names and shapes, so both are built from the helpers here. The recorder
adds a few event lists the live message leaves out (damage, deaths, respawns,
spawns and despawns); those builders live here too.
"""

from typing import Any

from .events import (
    ActionResult,
    DamageEvent,
    DeathEvent,
    EntityDespawnedEvent,
    EntitySpawnedEvent,
    ObjectChange,
    RespawnEvent,
    UtteranceEvent,
)
from .movement import MoveResult
from .state import Entity, World, WorldClock, WorldObject
from .types import Position


def position_state(position: Position) -> dict[str, int]:
    """JSON shape for a position."""
    return {"x": position.x, "y": position.y}


def entity_state(entity: Entity, tick: int) -> dict[str, Any]:
    """JSON shape for an entity sent to the viewer.

    `tick` is the tick the payload describes; the invitation flag
    (`open_to_talk`) is derived from it (docs/09, section 8.2).
    """
    return {
        "entity_id": entity.entity_id,
        "position": position_state(entity.position),
        "entity_type": entity.entity_type,
        "tags": list(entity.tags),
        "health": entity.health,
        "max_health": entity.max_health,
        "hunger": entity.hunger,
        "max_hunger": entity.max_hunger,
        "wielded": entity.wielded,
        "alive": entity.alive,
        "fatigue": entity.fatigue,
        "max_fatigue": entity.max_fatigue,
        "asleep": entity.asleep,
        "sleeping_on": entity.sleeping_on,
        "collapsed": entity.collapsed,
        "open_to_talk": entity.is_open_to_talk(tick),
        "inventory": {kind: count for kind, count in entity.inventory.items},
    }


def clock_payload(clock: WorldClock) -> dict[str, Any]:
    """JSON shape for the world clock (docs/10_metal_and_sleep.md)."""
    return {
        "day": clock.day,
        "tick_of_day": clock.tick_of_day,
        "day_length": clock.day_length,
        "night": clock.night,
    }


def entity_from_state(state: dict[str, Any], tick: int) -> Entity:
    """Rebuild an Entity from the JSON shape `entity_state` produces.

    The payload carries only the invitation flag, so an open invitation is
    rebuilt as one that lasts until just after `tick`, with no text.

    Raises:
        KeyError: If a required field is missing.
    """
    from .state import Inventory

    inventory = Inventory(
        items=tuple(
            (kind, int(count)) for kind, count in state.get("inventory", {}).items()
        )
    )
    position = state["position"]
    return Entity(
        entity_id=state["entity_id"],
        position=Position(x=int(position["x"]), y=int(position["y"])),
        entity_type=state.get("entity_type", "default"),
        tags=tuple(state.get("tags", ())),
        inventory=inventory,
        health=int(state.get("health", 0)),
        max_health=int(state.get("max_health", 20)),
        hunger=int(state.get("hunger", 0)),
        max_hunger=int(state.get("max_hunger", 100)),
        wielded=state.get("wielded", ""),
        alive=bool(state.get("alive", True)),
        fatigue=int(state.get("fatigue", 0)),
        max_fatigue=int(state.get("max_fatigue", 100)),
        asleep=bool(state.get("asleep", False)),
        sleeping_on=state.get("sleeping_on", ""),
        collapsed=bool(state.get("collapsed", False)),
        open_until_tick=tick + 1 if state.get("open_to_talk", False) else -1,
    )


def object_state(obj: WorldObject) -> dict[str, Any]:
    """JSON shape for a world object sent to the viewer."""
    return {
        "object_id": obj.object_id,
        "position": position_state(obj.position),
        "object_type": obj.object_type,
        "state": dict(obj.state),
    }


def object_from_state(state: dict[str, Any]) -> WorldObject:
    """Rebuild a WorldObject from the JSON shape `object_state` produces.

    Raises:
        KeyError: If a required field is missing.
    """
    position = state["position"]
    return WorldObject(
        object_id=state["object_id"],
        position=Position(x=int(position["x"]), y=int(position["y"])),
        object_type=state["object_type"],
        state=tuple(state.get("state", {}).items()),
    )


def move_payload(move: MoveResult) -> dict[str, Any]:
    """JSON shape for one movement result."""
    return {
        "entity_id": move.entity_id,
        "from": position_state(move.from_pos),
        "to": position_state(move.to_pos),
        "success": move.success,
    }


def object_change_payload(change: ObjectChange) -> dict[str, Any]:
    """JSON shape for one object state change."""
    return {
        "object_id": change.object_id,
        "field": change.field,
        "old_value": change.old_value,
        "new_value": change.new_value,
    }


def action_payload(action: ActionResult) -> dict[str, Any]:
    """JSON shape for one action result."""
    return {
        "entity_id": action.entity_id,
        "action_type": action.action_type,
        "success": action.success,
        "details": action.details,
    }


def utterance_payload(utterance: UtteranceEvent) -> dict[str, Any]:
    """JSON shape for one utterance."""
    return {
        "speaker_id": utterance.speaker_id,
        "channel": utterance.channel,
        "text": utterance.text,
        "position": position_state(utterance.position),
        "conversation_id": utterance.conversation_id,
        "open_to_talk": utterance.open_to_talk,
    }


def damage_payload(damage: DamageEvent) -> dict[str, Any]:
    """JSON shape for one damage event (recording only)."""
    return {
        "entity_id": damage.entity_id,
        "attacker_id": damage.attacker_id,
        "amount": damage.amount,
        "remaining_health": damage.remaining_health,
        "position": position_state(damage.position),
    }


def death_payload(death: DeathEvent) -> dict[str, Any]:
    """JSON shape for one death event (recording only)."""
    return {
        "entity_id": death.entity_id,
        "killer_id": death.killer_id,
        "position": position_state(death.position),
    }


def respawn_payload(respawn: RespawnEvent) -> dict[str, Any]:
    """JSON shape for one respawn event (recording only)."""
    return {
        "entity_id": respawn.entity_id,
        "position": position_state(respawn.position),
    }


def entity_spawned_payload(spawned: EntitySpawnedEvent) -> dict[str, Any]:
    """JSON shape for one entity spawn (recording only)."""
    return {
        "entity_id": spawned.entity_id,
        "entity_type": spawned.entity_type,
        "position": position_state(spawned.position),
    }


def entity_despawned_payload(despawned: EntityDespawnedEvent) -> dict[str, Any]:
    """JSON shape for one entity despawn (recording only)."""
    return {
        "entity_id": despawned.entity_id,
        "reason": despawned.reason,
        "position": position_state(despawned.position),
    }


def entity_updates(world: World) -> list[dict[str, Any]]:
    """Full state of every entity in the world."""
    return [
        entity_state(entity, world.tick) for entity in world.all_entities().values()
    ]
