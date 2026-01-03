"""Simple random-movement agent for testing gRPC integration."""

import random
import threading
import time
from typing import Iterator

import grpc
import structlog

from . import world_pb2 as pb
from . import world_pb2_grpc

logger = structlog.get_logger()

# All valid directions for random movement
DIRECTIONS = [
    pb.NORTH,
    pb.NORTHEAST,
    pb.EAST,
    pb.SOUTHEAST,
    pb.SOUTH,
    pb.SOUTHWEST,
    pb.WEST,
    pb.NORTHWEST,
]


class RandomAgent:
    """An agent that moves randomly each tick."""

    def __init__(
        self,
        server_address: str,
        entity_id: str,
        controller_id: str | None = None,
    ):
        self.server_address = server_address
        self.entity_id = entity_id
        self.controller_id = controller_id or f"random-agent-{entity_id}"

        self._channel: grpc.Channel | None = None
        self._lease_id: str | None = None
        self._running = False
        self._observation_thread: threading.Thread | None = None

    def connect(self) -> bool:
        """Connect to the world server and acquire a lease."""
        logger.info(
            "connecting",
            server=self.server_address,
            entity_id=self.entity_id,
        )

        self._channel = grpc.insecure_channel(self.server_address)

        # Acquire lease
        lease_stub = world_pb2_grpc.LeaseServiceStub(self._channel)
        response = lease_stub.AcquireLease(
            pb.AcquireLeaseRequest(
                entity_id=self.entity_id,
                controller_id=self.controller_id,
            )
        )

        if not response.success:
            logger.error("lease_acquisition_failed", reason=response.reason)
            return False

        self._lease_id = response.lease_id
        logger.info(
            "lease_acquired",
            lease_id=self._lease_id,
            expires_at_ms=response.expires_at_ms,
        )
        return True

    def run(self, duration_seconds: float | None = None) -> None:
        """Run the agent, processing observations and submitting intents.

        Args:
            duration_seconds: How long to run, or None for indefinitely.
        """
        if not self._lease_id:
            raise RuntimeError("Must connect() before run()")

        self._running = True
        start_time = time.time()

        logger.info("agent_starting", entity_id=self.entity_id)

        try:
            self._process_observations(duration_seconds, start_time)
        except grpc.RpcError as e:
            logger.error("grpc_error", code=e.code(), details=e.details())
        finally:
            self._running = False
            logger.info("agent_stopped", entity_id=self.entity_id)

    def _process_observations(
        self, duration_seconds: float | None, start_time: float
    ) -> None:
        """Stream observations and submit intents."""
        if not self._channel or not self._lease_id:
            return

        observation_stub = world_pb2_grpc.ObservationServiceStub(self._channel)
        action_stub = world_pb2_grpc.ActionServiceStub(self._channel)

        # Start observation stream
        observations: Iterator[pb.Observation] = observation_stub.StreamObservations(
            pb.StreamObservationsRequest(
                lease_id=self._lease_id,
                entity_id=self.entity_id,
            )
        )

        for observation in observations:
            if not self._running:
                break

            # Check duration limit
            if duration_seconds and (time.time() - start_time) >= duration_seconds:
                logger.info("duration_limit_reached")
                break

            # Log current state
            self_pos = observation.self.position
            logger.debug(
                "observation_received",
                tick_id=observation.tick_id,
                position=f"({self_pos.x}, {self_pos.y})",
                visible_entities=len(observation.visible_entities),
            )

            # Choose random direction
            direction = random.choice(DIRECTIONS)

            # Submit move intent for the CURRENT tick
            # (observation is sent at tick start, before deadline)
            response = action_stub.SubmitIntent(
                pb.SubmitIntentRequest(
                    lease_id=self._lease_id,
                    entity_id=self.entity_id,
                    tick_id=observation.tick_id,
                    intent=pb.Intent(move=pb.MoveIntent(direction=direction)),
                )
            )

            if response.accepted:
                logger.debug(
                    "intent_submitted",
                    tick_id=observation.tick_id,
                    direction=pb.Direction.Name(direction),
                )
            else:
                logger.warning(
                    "intent_rejected",
                    tick_id=observation.tick_id,
                    reason=response.reason,
                )

            # Renew lease periodically (every 10 seconds)
            self._maybe_renew_lease()

    def _maybe_renew_lease(self) -> None:
        """Renew lease if needed."""
        # Simple approach: renew every call (in production, track expiry time)
        if not self._channel or not self._lease_id:
            return

        lease_stub = world_pb2_grpc.LeaseServiceStub(self._channel)
        response = lease_stub.RenewLease(
            pb.RenewLeaseRequest(lease_id=self._lease_id)
        )

        if not response.success:
            logger.warning("lease_renewal_failed", reason=response.reason)

    def stop(self) -> None:
        """Signal the agent to stop."""
        self._running = False

    def disconnect(self) -> None:
        """Release lease and close connection."""
        if self._lease_id and self._channel:
            lease_stub = world_pb2_grpc.LeaseServiceStub(self._channel)
            lease_stub.ReleaseLease(
                pb.ReleaseLeaseRequest(lease_id=self._lease_id)
            )
            logger.info("lease_released", lease_id=self._lease_id)
            self._lease_id = None

        if self._channel:
            self._channel.close()
            self._channel = None


def discover_entities(server_address: str) -> list[pb.ControllableEntity]:
    """Discover all controllable entities on a server."""
    channel = grpc.insecure_channel(server_address)
    stub = world_pb2_grpc.EntityDiscoveryServiceStub(channel)

    response = stub.ListControllableEntities(
        pb.ListControllableEntitiesRequest()
    )

    entities = list(response.entities)
    channel.close()
    return entities


def main() -> None:
    """CLI entry point for the random agent."""
    import argparse

    parser = argparse.ArgumentParser(description="Random Movement Agent")
    parser.add_argument(
        "--server",
        type=str,
        default="localhost:50051",
        help="World server address",
    )
    parser.add_argument(
        "--entity",
        type=str,
        help="Entity ID to control (discovers first available if not specified)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Run duration in seconds (default: run forever)",
    )

    args = parser.parse_args()

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),
    )

    # Discover entity if not specified
    entity_id = args.entity
    if not entity_id:
        logger.info("discovering_entities", server=args.server)
        entities = discover_entities(args.server)
        if not entities:
            logger.error("no_entities_found")
            return

        # Pick first entity without an active lease
        for entity in entities:
            if not entity.has_active_lease:
                entity_id = entity.entity_id
                break

        if not entity_id:
            entity_id = entities[0].entity_id

        logger.info("selected_entity", entity_id=entity_id)

    # Create and run agent
    agent = RandomAgent(args.server, entity_id)

    if not agent.connect():
        return

    try:
        agent.run(duration_seconds=args.duration)
    except KeyboardInterrupt:
        logger.info("interrupted")
    finally:
        agent.disconnect()


if __name__ == "__main__":
    main()
