"""Entity discovery via gRPC."""

import time
from dataclasses import dataclass

import grpc
import structlog

from . import world_pb2 as pb
from . import world_pb2_grpc

logger = structlog.get_logger()


@dataclass
class DiscoveredEntity:
    """An entity discovered from the world server."""

    entity_id: str
    entity_type: str
    has_active_lease: bool


def discover_entities(
    server_address: str,
    timeout_seconds: float = 30.0,
) -> list[DiscoveredEntity]:
    """Discover all controllable entities from the world server.

    Args:
        server_address: gRPC server address (host:port)
        timeout_seconds: Connection timeout

    Returns:
        List of discovered entities

    Raises:
        grpc.RpcError: If connection fails
    """
    channel = grpc.insecure_channel(server_address)
    stub = world_pb2_grpc.EntityDiscoveryServiceStub(channel)

    try:
        response = stub.ListControllableEntities(
            pb.ListControllableEntitiesRequest(),
            timeout=timeout_seconds,
        )

        return [
            DiscoveredEntity(
                entity_id=e.entity_id,
                entity_type=e.entity_type,
                has_active_lease=e.has_active_lease,
            )
            for e in response.entities
        ]
    finally:
        channel.close()


def wait_for_server(
    server_address: str,
    timeout_seconds: float = 30.0,
    poll_interval_seconds: float = 1.0,
) -> bool:
    """Wait for the world server to become available.

    Args:
        server_address: gRPC server address (host:port)
        timeout_seconds: Maximum time to wait
        poll_interval_seconds: Time between connection attempts

    Returns:
        True if server is available, False if timeout
    """
    deadline = time.time() + timeout_seconds
    attempt = 0

    while time.time() < deadline:
        attempt += 1
        try:
            discover_entities(server_address, timeout_seconds=2.0)
            logger.info(
                "server_connected",
                server=server_address,
                attempts=attempt,
            )
            return True
        except grpc.RpcError:
            logger.debug(
                "server_not_ready",
                server=server_address,
                attempt=attempt,
            )
            time.sleep(poll_interval_seconds)

    logger.error(
        "server_connection_timeout",
        server=server_address,
        timeout_seconds=timeout_seconds,
    )
    return False
