"""ObservationService gRPC implementation."""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Iterator

import grpc
import structlog

from .. import world_pb2 as pb
from .. import world_pb2_grpc
from ..conversion import entity_to_proto, tile_to_proto
from ..lease import LeaseManager
from ..state import World
from ..tick import TickContext, TickLoop
from ..types import Position

logger = structlog.get_logger()


@dataclass
class ObservationSubscriber:
    """Tracks a subscriber waiting for observations."""

    entity_id: str
    lease_id: str
    queue: asyncio.Queue[pb.Observation] = field(default_factory=asyncio.Queue)


class ObservationServiceServicer(world_pb2_grpc.ObservationServiceServicer):
    """Implements the ObservationService for streaming observations."""

    def __init__(
        self,
        world: World,
        tick_loop: TickLoop,
        lease_manager: LeaseManager,
    ):
        self.world = world
        self.tick_loop = tick_loop
        self.lease_manager = lease_manager
        self._subscribers: dict[str, ObservationSubscriber] = {}

    def StreamObservations(
        self, request: pb.StreamObservationsRequest, context: grpc.ServicerContext
    ) -> Iterator[pb.Observation]:
        """Stream observations for an entity.

        Yields an Observation at the start of each tick.
        """
        lease_id = request.lease_id
        entity_id = request.entity_id

        # Validate lease
        if not self.lease_manager.is_valid_lease(lease_id, entity_id):
            context.set_code(grpc.StatusCode.PERMISSION_DENIED)
            context.set_details("Invalid lease")
            return

        logger.info(
            "observation_stream_started",
            entity_id=entity_id,
            peer=context.peer(),
        )

        # Create subscriber
        subscriber = ObservationSubscriber(entity_id=entity_id, lease_id=lease_id)
        self._subscribers[entity_id] = subscriber

        try:
            while context.is_active():
                # Check lease is still valid
                if not self.lease_manager.is_valid_lease(lease_id, entity_id):
                    logger.info("observation_stream_lease_expired", entity_id=entity_id)
                    break

                # Wait for next observation
                try:
                    observation = subscriber.queue.get_nowait()
                    yield observation
                except asyncio.QueueEmpty:
                    time.sleep(0.01)
                    continue

        except Exception as e:
            logger.warning("observation_stream_error", error=str(e))
        finally:
            self._subscribers.pop(entity_id, None)
            logger.info("observation_stream_ended", entity_id=entity_id)

    def broadcast_observations(self, context: TickContext) -> None:
        """Broadcast observations to all subscribers at the start of a tick.

        Called by the server when a new tick starts, before the deadline.
        Agents should submit intents for context.tick_id.
        """
        for entity_id, subscriber in list(self._subscribers.items()):
            try:
                observation = self._generate_observation(entity_id, context)
                if observation:
                    subscriber.queue.put_nowait(observation)
            except Exception as e:
                logger.warning(
                    "observation_generation_failed",
                    entity_id=entity_id,
                    error=str(e),
                )

    def _generate_observation(
        self, entity_id: str, context: TickContext
    ) -> pb.Observation | None:
        """Generate an observation for an entity.

        For Milestone 3, this is a basic implementation without line-of-sight.
        All entities and tiles are visible.
        """
        try:
            entity = self.world.get_entity(entity_id)
        except Exception:
            return None

        # Get self entity as proto
        self_proto = entity_to_proto(entity)

        # For Milestone 3: include all entities (no LOS filtering)
        visible_entities = [
            entity_to_proto(e)
            for e in self.world.all_entities().values()
            if e.entity_id != entity_id
        ]

        # For Milestone 3: include nearby tiles (simple radius)
        # In future milestones, this will use line-of-sight
        visible_tiles = self._get_nearby_tiles(entity.position, radius=5)

        # No events at tick start (events would be from previous tick)
        # Future: could include last tick's movement results here
        events: list[pb.ObservationEvent] = []

        # Note: 'self' is a Python keyword, so we need to construct message
        # and then set the field using setattr or pass via **kwargs
        observation = pb.Observation(
            tick_id=context.tick_id,
            deadline_ms=context.deadline_ms,
            visible_entities=visible_entities,
            visible_tiles=visible_tiles,
            events=events,
        )
        # Set 'self' field (Python keyword) using CopyFrom
        observation.self.CopyFrom(self_proto)
        return observation

    def _get_nearby_tiles(
        self, center: Position, radius: int = 5
    ) -> list[pb.Tile]:
        """Get tiles within a radius of the center position."""
        tiles = []
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                pos = Position(x=center.x + dx, y=center.y + dy)
                if self.world.in_bounds(pos):
                    tile = self.world.get_tile(pos)
                    tiles.append(tile_to_proto(tile))
        return tiles

