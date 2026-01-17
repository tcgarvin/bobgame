"""Tests for ViewerWebSocketService."""

import asyncio
import json

import pytest

from world.movement import MoveResult
from world.services.viewer_ws_service import ViewerWebSocketService
from world.state import Entity, World
from world.tick import TickConfig, TickContext, TickResult
from world.types import Position


@pytest.fixture
def world_with_entity() -> World:
    """Create a world with a single entity."""
    world = World(width=50, height=50)
    entity = Entity(
        entity_id="bob",
        position=Position(x=5, y=5),
        entity_type="player",
        tags=("test",),
    )
    world.add_entity(entity)
    return world


@pytest.fixture
def tick_config() -> TickConfig:
    """Create a tick config."""
    return TickConfig(tick_duration_ms=1000, intent_deadline_ms=500)


class TestSnapshotGeneration:
    """Tests for snapshot generation."""

    def test_generate_snapshot_metadata_only(
        self, world_with_entity: World, tick_config: TickConfig
    ) -> None:
        """Snapshot should contain world metadata, not entities/objects (those come via chunks)."""
        service = ViewerWebSocketService(world_with_entity, tick_config)
        snapshot = service._generate_snapshot()

        assert snapshot["type"] == "snapshot"
        # Entities and objects are now sent via chunk subscriptions
        assert "entities" not in snapshot
        assert "objects" not in snapshot
        # But chunk_size should be present
        assert snapshot["chunk_size"] == 32

    def test_generate_snapshot_includes_world_dimensions(
        self, world_with_entity: World, tick_config: TickConfig
    ) -> None:
        """Snapshot should include world width and height."""
        service = ViewerWebSocketService(world_with_entity, tick_config)
        snapshot = service._generate_snapshot()

        assert snapshot["world_size"]["width"] == 50
        assert snapshot["world_size"]["height"] == 50

    def test_generate_snapshot_includes_tick_info(
        self, world_with_entity: World, tick_config: TickConfig
    ) -> None:
        """Snapshot should include current tick and duration."""
        world_with_entity.advance_tick()
        world_with_entity.advance_tick()

        service = ViewerWebSocketService(world_with_entity, tick_config)
        snapshot = service._generate_snapshot()

        assert snapshot["tick_id"] == 2
        assert snapshot["tick_duration_ms"] == 1000

    def test_chunk_manager_initialized(
        self, world_with_entity: World, tick_config: TickConfig
    ) -> None:
        """Service should have a chunk manager with entities indexed."""
        service = ViewerWebSocketService(world_with_entity, tick_config)

        # Entity should be indexed in chunk (0, 0) since it's at position (5, 5)
        assert service.chunk_manager.get_entity_chunk("bob") == (0, 0)


class TestEventGeneration:
    """Tests for tick event generation."""

    def test_tick_started_event_format(
        self, world_with_entity: World, tick_config: TickConfig
    ) -> None:
        """Tick started event should match expected JSON schema."""
        service = ViewerWebSocketService(world_with_entity, tick_config)

        context = TickContext(
            tick_id=5,
            start_time_ms=1000000,
            deadline_ms=1000500,
        )

        # Call on_tick_start and check what was queued
        service.on_tick_start(context)

        # Get the queued event
        event = service._broadcast_queue.get_nowait()

        assert event["type"] == "tick_started"
        assert event["tick_id"] == 5
        assert event["tick_start_ms"] == 1000000
        assert event["deadline_ms"] == 1000500
        assert event["tick_duration_ms"] == 1000

    def test_tick_completed_event_includes_moves(
        self, world_with_entity: World, tick_config: TickConfig
    ) -> None:
        """Tick completed event should include all movement results."""
        service = ViewerWebSocketService(world_with_entity, tick_config)

        result = TickResult(
            tick_id=5,
            move_results=[
                MoveResult(
                    entity_id="bob",
                    success=True,
                    from_pos=Position(x=5, y=5),
                    to_pos=Position(x=6, y=5),
                ),
            ],
            duration_ms=10.5,
        )

        service.on_tick_complete(result)

        event = service._broadcast_queue.get_nowait()

        assert event["type"] == "tick_completed"
        assert event["tick_id"] == 5
        assert len(event["moves"]) == 1

        move = event["moves"][0]
        assert move["entity_id"] == "bob"
        assert move["success"] is True
        assert move["from"]["x"] == 5
        assert move["from"]["y"] == 5
        assert move["to"]["x"] == 6
        assert move["to"]["y"] == 5

    def test_tick_completed_event_empty_moves(
        self, world_with_entity: World, tick_config: TickConfig
    ) -> None:
        """Tick completed event should handle no moves."""
        service = ViewerWebSocketService(world_with_entity, tick_config)

        result = TickResult(
            tick_id=5,
            move_results=[],
            duration_ms=5.0,
        )

        service.on_tick_complete(result)

        event = service._broadcast_queue.get_nowait()

        assert event["type"] == "tick_completed"
        assert event["moves"] == []
        assert event["actions_processed"] == 0

    def test_tick_completed_with_failed_move(
        self, world_with_entity: World, tick_config: TickConfig
    ) -> None:
        """Tick completed event should include failed moves."""
        service = ViewerWebSocketService(world_with_entity, tick_config)

        result = TickResult(
            tick_id=5,
            move_results=[
                MoveResult(
                    entity_id="bob",
                    success=False,
                    from_pos=Position(x=5, y=5),
                    to_pos=Position(x=5, y=5),  # Stayed in place
                    failure_reason="destination_occupied",
                ),
            ],
            duration_ms=10.0,
        )

        service.on_tick_complete(result)

        event = service._broadcast_queue.get_nowait()

        move = event["moves"][0]
        assert move["success"] is False
        assert move["from"]["x"] == 5
        assert move["to"]["x"] == 5  # Same position


class TestClientCount:
    """Tests for client tracking."""

    def test_initial_client_count_zero(
        self, world_with_entity: World, tick_config: TickConfig
    ) -> None:
        """Initial client count should be zero."""
        service = ViewerWebSocketService(world_with_entity, tick_config)
        assert service.client_count == 0


class TestEventSerialization:
    """Tests for JSON serialization of events."""

    def test_snapshot_is_valid_json(
        self, world_with_entity: World, tick_config: TickConfig
    ) -> None:
        """Snapshot should be valid JSON."""
        service = ViewerWebSocketService(world_with_entity, tick_config)
        snapshot = service._generate_snapshot()

        # Should not raise
        json_str = json.dumps(snapshot)
        parsed = json.loads(json_str)

        assert parsed == snapshot

    def test_tick_events_are_valid_json(
        self, world_with_entity: World, tick_config: TickConfig
    ) -> None:
        """Tick events should be valid JSON."""
        service = ViewerWebSocketService(world_with_entity, tick_config)

        context = TickContext(tick_id=1, start_time_ms=1000, deadline_ms=1500)
        service.on_tick_start(context)
        event = service._broadcast_queue.get_nowait()

        json_str = json.dumps(event)
        parsed = json.loads(json_str)

        assert parsed == event
