"""EntityDiscoveryService gRPC implementation."""

import grpc
import structlog

from .. import world_pb2 as pb
from .. import world_pb2_grpc
from ..lease import LeaseManager
from ..state import World

logger = structlog.get_logger()


class EntityDiscoveryServiceServicer(world_pb2_grpc.EntityDiscoveryServiceServicer):
    """Implements the EntityDiscoveryService for listing controllable entities."""

    def __init__(self, world: World, lease_manager: LeaseManager):
        self.world = world
        self.lease_manager = lease_manager
        # Track spawn ticks for entities (entity_id -> tick when added)
        self._spawn_ticks: dict[str, int] = {}

    def register_entity_spawn(self, entity_id: str, spawn_tick: int) -> None:
        """Register when an entity was spawned."""
        self._spawn_ticks[entity_id] = spawn_tick

    def ListControllableEntities(
        self, request: pb.ListControllableEntitiesRequest, context: grpc.ServicerContext
    ) -> pb.ControllableEntitiesResponse:
        """List all entities that can be controlled by agents."""
        entities = []

        for entity_id, entity in self.world.all_entities().items():
            # Check if entity has an active lease
            lease = self.lease_manager.get_lease_for_entity(entity_id)
            has_active_lease = lease is not None

            # Get spawn tick (default to 0 if unknown)
            spawn_tick = self._spawn_ticks.get(entity_id, 0)

            controllable = pb.ControllableEntity(
                entity_id=entity_id,
                entity_type=entity.entity_type,
                tags=list(entity.tags),
                spawn_tick=spawn_tick,
                has_active_lease=has_active_lease,
            )
            entities.append(controllable)

        logger.debug(
            "list_controllable_entities",
            count=len(entities),
            peer=context.peer(),
        )

        return pb.ControllableEntitiesResponse(entities=entities)
