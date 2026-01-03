"""LeaseService gRPC implementation."""

import grpc
import structlog

from .. import world_pb2 as pb
from .. import world_pb2_grpc
from ..lease import LeaseManager
from ..state import World

logger = structlog.get_logger()


class LeaseServiceServicer(world_pb2_grpc.LeaseServiceServicer):
    """Implements the LeaseService for entity control leases."""

    def __init__(self, world: World, lease_manager: LeaseManager):
        self.world = world
        self.lease_manager = lease_manager

    def AcquireLease(
        self, request: pb.AcquireLeaseRequest, context: grpc.ServicerContext
    ) -> pb.LeaseResponse:
        """Acquire a lease for an entity."""
        entity_id = request.entity_id
        controller_id = request.controller_id

        if not entity_id:
            return pb.LeaseResponse(success=False, reason="entity_id required")
        if not controller_id:
            return pb.LeaseResponse(success=False, reason="controller_id required")

        # Verify entity exists
        try:
            self.world.get_entity(entity_id)
        except Exception:
            return pb.LeaseResponse(success=False, reason="entity not found")

        result = self.lease_manager.acquire(entity_id, controller_id)

        if isinstance(result, str):
            return pb.LeaseResponse(success=False, reason=result)

        return pb.LeaseResponse(
            success=True,
            lease_id=result.lease_id,
            expires_at_ms=result.expires_at_ms,
        )

    def RenewLease(
        self, request: pb.RenewLeaseRequest, context: grpc.ServicerContext
    ) -> pb.LeaseResponse:
        """Renew an existing lease."""
        lease_id = request.lease_id

        if not lease_id:
            return pb.LeaseResponse(success=False, reason="lease_id required")

        result = self.lease_manager.renew(lease_id)

        if isinstance(result, str):
            return pb.LeaseResponse(success=False, reason=result)

        return pb.LeaseResponse(
            success=True,
            lease_id=result.lease_id,
            expires_at_ms=result.expires_at_ms,
        )

    def ReleaseLease(
        self, request: pb.ReleaseLeaseRequest, context: grpc.ServicerContext
    ) -> pb.ReleaseLeaseResponse:
        """Release a lease."""
        lease_id = request.lease_id

        if not lease_id:
            return pb.ReleaseLeaseResponse(success=False)

        success = self.lease_manager.release(lease_id)
        return pb.ReleaseLeaseResponse(success=success)
