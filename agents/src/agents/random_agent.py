"""Simple foraging agent with state machine behavior.

Originally a random-movement agent, now upgraded to seek out berries,
collect them, and occasionally eat them.
"""

import random
import threading
import time
from enum import Enum, auto
from typing import Iterator

import grpc
import structlog

from . import world_pb2 as pb
from . import world_pb2_grpc

logger = structlog.get_logger()


class AgentState(Enum):
    """States for the agent's state machine."""

    WANDER = auto()  # No berries visible, move randomly
    SEEK = auto()  # Moving toward a visible bush with berries
    COLLECT = auto()  # At a bush with berries, collecting
    EAT = auto()  # Consuming berries from inventory


# Direction vectors for movement
DIRECTION_DELTAS = {
    pb.NORTH: (0, -1),
    pb.NORTHEAST: (1, -1),
    pb.EAST: (1, 0),
    pb.SOUTHEAST: (1, 1),
    pb.SOUTH: (0, 1),
    pb.SOUTHWEST: (-1, 1),
    pb.WEST: (-1, 0),
    pb.NORTHWEST: (-1, -1),
}

# All valid directions for random movement
DIRECTIONS = list(DIRECTION_DELTAS.keys())


def manhattan_distance(x1: int, y1: int, x2: int, y2: int) -> int:
    """Calculate Manhattan distance between two positions."""
    return abs(x2 - x1) + abs(y2 - y1)


def direction_toward(from_x: int, from_y: int, to_x: int, to_y: int) -> pb.Direction:
    """
    Get the best direction to move from one position toward another.

    Uses a greedy bee-line approach: picks the direction that minimizes
    distance to the target.
    """
    best_direction = pb.NORTH
    best_distance = float("inf")

    for direction, (dx, dy) in DIRECTION_DELTAS.items():
        new_x = from_x + dx
        new_y = from_y + dy
        distance = manhattan_distance(new_x, new_y, to_x, to_y)
        if distance < best_distance:
            best_distance = distance
            best_direction = direction

    return best_direction


class SimpleAgent:
    """An agent that seeks out berries, collects them, and eats them.

    Uses a state machine with four states:
    - WANDER: Move randomly when no berries visible
    - SEEK: Move toward the nearest visible bush with berries
    - COLLECT: Collect berries when standing on a bush
    - EAT: Occasionally consume berries from inventory
    """

    def __init__(
        self,
        server_address: str,
        entity_id: str,
        controller_id: str | None = None,
        eat_probability: float = 0.1,
    ):
        """
        Initialize the agent.

        Args:
            server_address: gRPC server address (host:port)
            entity_id: Entity to control
            controller_id: Unique identifier for this controller
            eat_probability: Chance to eat a berry each tick when idle with berries
        """
        self.server_address = server_address
        self.entity_id = entity_id
        self.controller_id = controller_id or f"simple-agent-{entity_id}"
        self.eat_probability = eat_probability

        self._channel: grpc.Channel | None = None
        self._lease_id: str | None = None
        self._running = False
        self._observation_thread: threading.Thread | None = None

        # State machine
        self._state = AgentState.WANDER
        self._target_object_id: str | None = None

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

        logger.info("agent_starting", entity_id=self.entity_id, agent_type="simple")

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

            # Update state machine and decide action
            self._update_state(observation)
            intent = self._decide_action(observation)

            # Log current state
            self_pos = observation.self.position
            inventory_berries = self._get_berry_count(observation)
            logger.debug(
                "tick",
                tick_id=observation.tick_id,
                position=f"({self_pos.x}, {self_pos.y})",
                state=self._state.name,
                berries=inventory_berries,
            )

            # Submit intent for the CURRENT tick
            response = action_stub.SubmitIntent(
                pb.SubmitIntentRequest(
                    lease_id=self._lease_id,
                    entity_id=self.entity_id,
                    tick_id=observation.tick_id,
                    intent=intent,
                )
            )

            if response.accepted:
                self._log_intent(observation.tick_id, intent)
            else:
                logger.warning(
                    "intent_rejected",
                    tick_id=observation.tick_id,
                    reason=response.reason,
                )

            # Renew lease periodically
            self._maybe_renew_lease()

    def _get_berry_count(self, observation: pb.Observation) -> int:
        """Get the number of berries in entity's inventory."""
        for item in observation.self.inventory.items:
            if item.kind == "berry":
                return item.quantity
        return 0

    def _find_bushes_with_berries(
        self, observation: pb.Observation
    ) -> list[pb.WorldObject]:
        """Find all visible bushes that have berries."""
        bushes = []
        for obj in observation.visible_objects:
            if obj.object_type == "bush":
                berry_count = int(obj.state.get("berry_count", "0"))
                if berry_count > 0:
                    bushes.append(obj)
        return bushes

    def _find_bush_at_position(
        self, observation: pb.Observation, x: int, y: int
    ) -> pb.WorldObject | None:
        """Find a bush with berries at the given position."""
        for obj in observation.visible_objects:
            if obj.object_type == "bush":
                if obj.position.x == x and obj.position.y == y:
                    berry_count = int(obj.state.get("berry_count", "0"))
                    if berry_count > 0:
                        return obj
        return None

    def _update_state(self, observation: pb.Observation) -> None:
        """Update the state machine based on current observation."""
        self_pos = observation.self.position
        bushes_with_berries = self._find_bushes_with_berries(observation)
        berry_count = self._get_berry_count(observation)

        # Check if we're at a bush with berries
        bush_here = self._find_bush_at_position(
            observation, self_pos.x, self_pos.y
        )

        if bush_here:
            # At a bush with berries -> COLLECT
            self._state = AgentState.COLLECT
            self._target_object_id = bush_here.object_id
        elif bushes_with_berries:
            # Can see bushes with berries -> SEEK the nearest one
            nearest_bush = min(
                bushes_with_berries,
                key=lambda b: manhattan_distance(
                    self_pos.x, self_pos.y, b.position.x, b.position.y
                ),
            )
            self._state = AgentState.SEEK
            self._target_object_id = nearest_bush.object_id
        elif berry_count > 0 and random.random() < self.eat_probability:
            # Have berries and randomly decided to eat
            self._state = AgentState.EAT
            self._target_object_id = None
        else:
            # Nothing to do, wander
            self._state = AgentState.WANDER
            self._target_object_id = None

    def _decide_action(self, observation: pb.Observation) -> pb.Intent:
        """Decide what action to take based on current state."""
        self_pos = observation.self.position

        if self._state == AgentState.COLLECT:
            # Collect from the bush we're standing on
            return pb.Intent(
                collect=pb.CollectIntent(
                    object_id=self._target_object_id,
                    item_type="berry",
                )
            )

        elif self._state == AgentState.EAT:
            # Eat a berry from inventory
            return pb.Intent(
                eat=pb.EatIntent(
                    item_type="berry",
                    amount=1,
                )
            )

        elif self._state == AgentState.SEEK:
            # Move toward the target bush
            target_bush = None
            for obj in observation.visible_objects:
                if obj.object_id == self._target_object_id:
                    target_bush = obj
                    break

            if target_bush:
                direction = direction_toward(
                    self_pos.x,
                    self_pos.y,
                    target_bush.position.x,
                    target_bush.position.y,
                )
                return pb.Intent(move=pb.MoveIntent(direction=direction))
            else:
                # Target no longer visible, wander
                direction = random.choice(DIRECTIONS)
                return pb.Intent(move=pb.MoveIntent(direction=direction))

        else:  # WANDER
            direction = random.choice(DIRECTIONS)
            return pb.Intent(move=pb.MoveIntent(direction=direction))

    def _log_intent(self, tick_id: int, intent: pb.Intent) -> None:
        """Log the submitted intent."""
        if intent.HasField("move"):
            logger.debug(
                "intent",
                tick_id=tick_id,
                action="move",
                direction=pb.Direction.Name(intent.move.direction),
            )
        elif intent.HasField("collect"):
            logger.info(
                "intent",
                tick_id=tick_id,
                action="collect",
                object_id=intent.collect.object_id,
            )
        elif intent.HasField("eat"):
            logger.info(
                "intent",
                tick_id=tick_id,
                action="eat",
                item=intent.eat.item_type,
            )

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
    """CLI entry point for the simple agent."""
    import argparse

    parser = argparse.ArgumentParser(description="Simple Foraging Agent")
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
    parser.add_argument(
        "--eat-probability",
        type=float,
        default=0.1,
        help="Probability of eating a berry when idle (default: 0.1)",
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
    agent = SimpleAgent(
        args.server,
        entity_id,
        eat_probability=args.eat_probability,
    )

    if not agent.connect():
        return

    try:
        agent.run(duration_seconds=args.duration)
    except KeyboardInterrupt:
        logger.info("interrupted")
    finally:
        agent.disconnect()


# Backward compatibility alias
RandomAgent = SimpleAgent


if __name__ == "__main__":
    main()
