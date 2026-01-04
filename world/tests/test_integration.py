"""End-to-end integration tests for gRPC server and agent communication."""

import asyncio
import threading
import time
from concurrent import futures

import grpc
import pytest

from world import world_pb2 as pb
from world import world_pb2_grpc
from world.lease import LeaseManager
from world.server import WorldServer
from world.services import (
    ActionServiceServicer,
    EntityDiscoveryServiceServicer,
    LeaseServiceServicer,
)
from world.state import Entity, World
from world.tick import TickConfig, TickLoop
from world.types import Direction, Position


class TestLeaseServiceIntegration:
    """Integration tests for LeaseService via gRPC."""

    def test_acquire_and_release_lease(self) -> None:
        """Test acquiring and releasing a lease via gRPC."""
        # Setup
        world = World(width=10, height=10)
        world.add_entity(Entity(entity_id="bob", position=Position(x=5, y=5)))
        lease_manager = LeaseManager()

        # Create gRPC server
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
        world_pb2_grpc.add_LeaseServiceServicer_to_server(
            LeaseServiceServicer(world, lease_manager), server
        )
        port = server.add_insecure_port("[::]:0")  # Random available port
        server.start()

        try:
            # Create client
            channel = grpc.insecure_channel(f"localhost:{port}")
            stub = world_pb2_grpc.LeaseServiceStub(channel)

            # Acquire lease
            response = stub.AcquireLease(
                pb.AcquireLeaseRequest(
                    entity_id="bob",
                    controller_id="test-controller",
                )
            )

            assert response.success
            assert response.lease_id
            assert response.expires_at_ms > 0

            lease_id = response.lease_id

            # Renew lease
            renew_response = stub.RenewLease(
                pb.RenewLeaseRequest(lease_id=lease_id)
            )

            assert renew_response.success
            assert renew_response.lease_id == lease_id

            # Release lease
            release_response = stub.ReleaseLease(
                pb.ReleaseLeaseRequest(lease_id=lease_id)
            )

            assert release_response.success

            # Verify lease is gone
            renew_again = stub.RenewLease(
                pb.RenewLeaseRequest(lease_id=lease_id)
            )
            assert not renew_again.success

            channel.close()
        finally:
            server.stop(0)

    def test_acquire_lease_entity_not_found(self) -> None:
        """Test acquiring a lease for nonexistent entity."""
        world = World(width=10, height=10)
        lease_manager = LeaseManager()

        server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
        world_pb2_grpc.add_LeaseServiceServicer_to_server(
            LeaseServiceServicer(world, lease_manager), server
        )
        port = server.add_insecure_port("[::]:0")
        server.start()

        try:
            channel = grpc.insecure_channel(f"localhost:{port}")
            stub = world_pb2_grpc.LeaseServiceStub(channel)

            response = stub.AcquireLease(
                pb.AcquireLeaseRequest(
                    entity_id="nonexistent",
                    controller_id="test-controller",
                )
            )

            assert not response.success
            assert "not found" in response.reason

            channel.close()
        finally:
            server.stop(0)


class TestEntityDiscoveryIntegration:
    """Integration tests for EntityDiscoveryService."""

    def test_list_controllable_entities(self) -> None:
        """Test listing controllable entities."""
        world = World(width=10, height=10)
        world.add_entity(
            Entity(entity_id="bob", position=Position(x=5, y=5), entity_type="player")
        )
        world.add_entity(
            Entity(
                entity_id="alice",
                position=Position(x=3, y=3),
                entity_type="npc",
                tags=("friendly",),
            )
        )
        lease_manager = LeaseManager()

        server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
        discovery_service = EntityDiscoveryServiceServicer(world, lease_manager)
        # Register spawn ticks
        discovery_service.register_entity_spawn("bob", 0)
        discovery_service.register_entity_spawn("alice", 5)

        world_pb2_grpc.add_EntityDiscoveryServiceServicer_to_server(
            discovery_service, server
        )
        port = server.add_insecure_port("[::]:0")
        server.start()

        try:
            channel = grpc.insecure_channel(f"localhost:{port}")
            stub = world_pb2_grpc.EntityDiscoveryServiceStub(channel)

            response = stub.ListControllableEntities(
                pb.ListControllableEntitiesRequest()
            )

            assert len(response.entities) == 2

            entities_by_id = {e.entity_id: e for e in response.entities}

            assert "bob" in entities_by_id
            assert entities_by_id["bob"].entity_type == "player"
            assert entities_by_id["bob"].spawn_tick == 0
            assert not entities_by_id["bob"].has_active_lease

            assert "alice" in entities_by_id
            assert entities_by_id["alice"].entity_type == "npc"
            assert list(entities_by_id["alice"].tags) == ["friendly"]
            assert entities_by_id["alice"].spawn_tick == 5

            channel.close()
        finally:
            server.stop(0)


class TestActionServiceIntegration:
    """Integration tests for ActionService with tick loop."""

    @pytest.mark.asyncio
    async def test_submit_move_intent(self) -> None:
        """Test submitting a move intent via gRPC."""
        world = World(width=10, height=10)
        world.add_entity(Entity(entity_id="bob", position=Position(x=5, y=5)))

        lease_manager = LeaseManager()
        tick_config = TickConfig(tick_duration_ms=100, intent_deadline_ms=50)
        tick_loop = TickLoop(world, config=tick_config)

        # Acquire lease directly
        lease = lease_manager.acquire("bob", "test-controller")
        assert not isinstance(lease, str)
        lease_id = lease.lease_id

        server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
        world_pb2_grpc.add_ActionServiceServicer_to_server(
            ActionServiceServicer(tick_loop, lease_manager), server
        )
        port = server.add_insecure_port("[::]:0")
        server.start()

        # Start tick loop in background
        tick_task = asyncio.create_task(tick_loop.run())

        try:
            # Wait for tick to start
            await asyncio.sleep(0.02)

            channel = grpc.insecure_channel(f"localhost:{port}")
            stub = world_pb2_grpc.ActionServiceStub(channel)

            # Submit move intent
            current_tick = tick_loop.current_tick
            response = stub.SubmitIntent(
                pb.SubmitIntentRequest(
                    lease_id=lease_id,
                    entity_id="bob",
                    tick_id=current_tick,
                    intent=pb.Intent(move=pb.MoveIntent(direction=pb.NORTH)),
                )
            )

            assert response.accepted

            # Wait for tick to process
            await asyncio.sleep(0.15)

            # Entity should have moved north
            entity = world.get_entity("bob")
            assert entity.position == Position(x=5, y=4)

            channel.close()
        finally:
            tick_loop.stop()
            await tick_task
            server.stop(0)

    @pytest.mark.asyncio
    async def test_submit_intent_invalid_lease(self) -> None:
        """Test submitting with invalid lease."""
        world = World(width=10, height=10)
        world.add_entity(Entity(entity_id="bob", position=Position(x=5, y=5)))

        lease_manager = LeaseManager()
        tick_config = TickConfig(tick_duration_ms=100, intent_deadline_ms=50)
        tick_loop = TickLoop(world, config=tick_config)

        server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
        world_pb2_grpc.add_ActionServiceServicer_to_server(
            ActionServiceServicer(tick_loop, lease_manager), server
        )
        port = server.add_insecure_port("[::]:0")
        server.start()

        tick_task = asyncio.create_task(tick_loop.run())

        try:
            await asyncio.sleep(0.02)

            channel = grpc.insecure_channel(f"localhost:{port}")
            stub = world_pb2_grpc.ActionServiceStub(channel)

            response = stub.SubmitIntent(
                pb.SubmitIntentRequest(
                    lease_id="invalid-lease",
                    entity_id="bob",
                    tick_id=tick_loop.current_tick,
                    intent=pb.Intent(move=pb.MoveIntent(direction=pb.NORTH)),
                )
            )

            assert not response.accepted
            assert "invalid_lease" in response.reason

            channel.close()
        finally:
            tick_loop.stop()
            await tick_task
            server.stop(0)


class TestFullServerIntegration:
    """Integration tests using the full WorldServer."""

    @pytest.mark.asyncio
    async def test_world_server_lifecycle(self) -> None:
        """Test starting and stopping the world server."""
        world = World(width=10, height=10)
        config = TickConfig(tick_duration_ms=50, intent_deadline_ms=25)

        server = WorldServer(world, port=50099, ws_port=18765, tick_config=config)

        server.add_entity(Entity(entity_id="bob", position=Position(x=5, y=5)))

        await server.start()

        try:
            # Wait a tick
            await asyncio.sleep(0.1)

            # Verify tick loop is running
            assert server.tick_loop.is_running
            assert world.tick >= 1

        finally:
            await server.stop()

        assert not server.tick_loop.is_running

    @pytest.mark.asyncio
    async def test_agent_moves_entity_via_grpc(self) -> None:
        """End-to-end test: agent connects, acquires lease, moves entity."""
        world = World(width=10, height=10)
        config = TickConfig(tick_duration_ms=100, intent_deadline_ms=50)

        server = WorldServer(world, port=50098, ws_port=18766, tick_config=config)
        server.add_entity(Entity(entity_id="bob", position=Position(x=5, y=5)))

        initial_position = world.get_entity("bob").position

        await server.start()

        try:
            # Give server time to start
            await asyncio.sleep(0.05)

            # Connect as agent
            channel = grpc.insecure_channel("localhost:50098")

            # Discover entity
            discovery_stub = world_pb2_grpc.EntityDiscoveryServiceStub(channel)
            entities = discovery_stub.ListControllableEntities(
                pb.ListControllableEntitiesRequest()
            )
            assert len(entities.entities) == 1
            assert entities.entities[0].entity_id == "bob"

            # Acquire lease
            lease_stub = world_pb2_grpc.LeaseServiceStub(channel)
            lease_response = lease_stub.AcquireLease(
                pb.AcquireLeaseRequest(
                    entity_id="bob",
                    controller_id="test-agent",
                )
            )
            assert lease_response.success
            lease_id = lease_response.lease_id

            # Submit move intent
            action_stub = world_pb2_grpc.ActionServiceStub(channel)

            # Wait for a tick to be in progress
            for _ in range(20):
                await asyncio.sleep(0.01)
                ctx = server.tick_loop.current_context
                if ctx is not None and not ctx.is_past_deadline():
                    break

            ctx = server.tick_loop.current_context
            assert ctx is not None, "No tick context available"

            move_response = action_stub.SubmitIntent(
                pb.SubmitIntentRequest(
                    lease_id=lease_id,
                    entity_id="bob",
                    tick_id=ctx.tick_id,
                    intent=pb.Intent(move=pb.MoveIntent(direction=pb.SOUTH)),
                )
            )
            assert move_response.accepted, f"Intent rejected: {move_response.reason}"

            # Wait for tick to complete
            await asyncio.sleep(0.15)

            # Verify entity moved
            new_position = world.get_entity("bob").position
            assert new_position != initial_position
            assert new_position == Position(x=5, y=6)  # Moved south

            # Release lease
            lease_stub.ReleaseLease(pb.ReleaseLeaseRequest(lease_id=lease_id))

            channel.close()

        finally:
            await server.stop()
