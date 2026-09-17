"""Tests for the extended viewer tick_completed / snapshot payloads."""

from typing import Any

import pytest

from world.events import (
    EntityDespawnedEvent,
    EntitySpawnedEvent,
    ObjectAddedEvent,
    ObjectRemovedEvent,
    RespawnEvent,
    UtteranceEvent,
)
from world.events import ActionResult
from world.movement import MoveResult
from world.services.viewer_ws_service import ViewerWebSocketService
from world.state import Entity, Inventory, World, WorldObject
from world.tick import TickConfig, TickResult
from world.types import Position


@pytest.fixture
def world() -> World:
    world = World(width=100, height=100)
    world.add_entity(
        Entity(
            entity_id="ada",
            position=Position(x=10, y=10),
            entity_type="player",
            inventory=Inventory(items=(("wood", 3),)),
            health=14,
            hunger=42,
            wielded="axe",
        )
    )
    return world


@pytest.fixture
def service(world: World) -> ViewerWebSocketService:
    return ViewerWebSocketService(world, TickConfig(tick_duration_ms=2000))


def _drain(service: ViewerWebSocketService) -> list[dict[str, Any]]:
    events = []
    while not service._broadcast_queue.empty():
        events.append(service._broadcast_queue.get_nowait())
    return events


class TestSnapshot:
    def test_settlement_is_null_when_unset(
        self, service: ViewerWebSocketService
    ) -> None:
        assert service._generate_snapshot()["settlement"] is None

    def test_settlement_position(
        self, world: World, service: ViewerWebSocketService
    ) -> None:
        world.settlement = Position(x=168, y=904)
        assert service._generate_snapshot()["settlement"] == {"x": 168, "y": 904}


class TestTickCompleted:
    def test_payload_shape(self, world: World, service: ViewerWebSocketService) -> None:
        chest = WorldObject(
            object_id="chest_1",
            position=Position(x=11, y=10),
            object_type="chest",
            state=(("contents", '{"wood": 2}'),),
        )
        service.on_tick_complete(
            TickResult(
                tick_id=5,
                move_results=[
                    MoveResult(
                        entity_id="ada",
                        success=True,
                        from_pos=Position(x=9, y=10),
                        to_pos=Position(x=10, y=10),
                    )
                ],
                action_results=[ActionResult("ada", "craft", True, "made an axe")],
                utterances=[
                    UtteranceEvent("ada", "local", "hi", Position(x=10, y=10)),
                    UtteranceEvent("ada", "thought", "hmm", Position(x=10, y=10)),
                    UtteranceEvent(
                        "ada",
                        "conversation",
                        "about the wall",
                        Position(x=10, y=10),
                        "conv_3",
                    ),
                ],
                objects_added=[ObjectAddedEvent(chest)],
                objects_removed=[ObjectRemovedEvent("tree_9", Position(x=12, y=12))],
            )
        )

        event = _drain(service)[0]

        assert event["type"] == "tick_completed"
        assert event["tick_id"] == 5
        assert event["moves"][0]["entity_id"] == "ada"
        assert event["entity_updates"] == [
            {
                "entity_id": "ada",
                "position": {"x": 10, "y": 10},
                "entity_type": "player",
                "tags": [],
                "health": 14,
                "max_health": 20,
                "hunger": 42,
                "max_hunger": 100,
                "wielded": "axe",
                "alive": True,
                "inventory": {"wood": 3},
            }
        ]
        assert event["actions"] == [
            {
                "entity_id": "ada",
                "action_type": "craft",
                "success": True,
                "details": "made an axe",
            }
        ]
        # Every channel reaches the viewer, each with its conversation id.
        assert [u["channel"] for u in event["utterances"]] == [
            "local",
            "thought",
            "conversation",
        ]
        assert [u["conversation_id"] for u in event["utterances"]] == [
            "",
            "",
            "conv_3",
        ]
        assert event["objects_added"] == [
            {
                "object_id": "chest_1",
                "position": {"x": 11, "y": 10},
                "object_type": "chest",
                "state": {"contents": '{"wood": 2}'},
            }
        ]
        assert event["objects_removed"] == ["tree_9"]

    def test_added_and_removed_objects_update_the_chunk_index(
        self, world: World, service: ViewerWebSocketService
    ) -> None:
        chest = WorldObject(
            object_id="chest_1",
            position=Position(x=70, y=70),
            object_type="chest",
        )
        service.on_tick_complete(
            TickResult(
                tick_id=1,
                move_results=[],
                objects_added=[ObjectAddedEvent(chest)],
            )
        )
        assert service.chunk_manager.get_object_chunk("chest_1") == (2, 2)

        service.on_tick_complete(
            TickResult(
                tick_id=2,
                move_results=[],
                objects_removed=[ObjectRemovedEvent("chest_1", chest.position)],
            )
        )
        assert service.chunk_manager.get_object_chunk("chest_1") is None


class TestSpawnAndDespawn:
    def test_spawn_emits_message_and_indexes_entity(
        self, world: World, service: ViewerWebSocketService
    ) -> None:
        world.add_entity(
            Entity(
                entity_id="wolf_1",
                position=Position(x=40, y=40),
                entity_type="wolf",
                health=10,
                max_health=10,
            )
        )
        service.on_tick_complete(
            TickResult(
                tick_id=3,
                move_results=[],
                entities_spawned=[
                    EntitySpawnedEvent("wolf_1", Position(x=40, y=40), "wolf")
                ],
            )
        )

        spawned = [e for e in _drain(service) if e["type"] == "entity_spawned"]

        assert spawned[0]["entity"]["entity_id"] == "wolf_1"
        assert spawned[0]["entity"]["entity_type"] == "wolf"
        assert service.chunk_manager.get_entity_chunk("wolf_1") == (1, 1)

    def test_despawn_emits_message_and_drops_index(
        self, world: World, service: ViewerWebSocketService
    ) -> None:
        service.chunk_manager.add_entity("wolf_1", Position(x=40, y=40))
        service.on_tick_complete(
            TickResult(
                tick_id=4,
                move_results=[],
                entities_despawned=[
                    EntityDespawnedEvent("wolf_1", Position(x=40, y=40), "far_away")
                ],
            )
        )

        despawned = [e for e in _drain(service) if e["type"] == "entity_despawned"]

        assert despawned[0]["entity_id"] == "wolf_1"
        assert despawned[0]["reason"] == "far_away"
        assert service.chunk_manager.get_entity_chunk("wolf_1") is None

    def test_respawn_emits_spawn_and_moves_the_index(
        self, world: World, service: ViewerWebSocketService
    ) -> None:
        service.chunk_manager.add_entity("ada", Position(x=10, y=10))
        world._entities["ada"] = world.get_entity("ada").with_position(
            Position(x=80, y=80)
        )
        service.on_tick_complete(
            TickResult(
                tick_id=6,
                move_results=[],
                respawns=[RespawnEvent("ada", Position(x=80, y=80))],
            )
        )

        spawned = [e for e in _drain(service) if e["type"] == "entity_spawned"]

        assert spawned[0]["entity"]["position"] == {"x": 80, "y": 80}
        assert service.chunk_manager.get_entity_chunk("ada") == (2, 2)
