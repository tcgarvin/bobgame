"""TickService gRPC implementation."""

import asyncio
from typing import Iterator

import grpc
import structlog

from .. import world_pb2 as pb
from .. import world_pb2_grpc
from ..tick import TickLoop

logger = structlog.get_logger()

# Version string for world protocol
WORLD_VERSION = "0.3.0"


class TickServiceServicer(world_pb2_grpc.TickServiceServicer):
    """Implements the TickService for streaming tick events."""

    def __init__(self, tick_loop: TickLoop):
        self.tick_loop = tick_loop
        self._subscribers: list[asyncio.Queue[pb.TickEvent]] = []

    def StreamTicks(
        self, request: pb.StreamTicksRequest, context: grpc.ServicerContext
    ) -> Iterator[pb.TickEvent]:
        """Stream tick events to connected clients.

        Yields TickEvent for each tick, including timing information.
        """
        logger.info("tick_stream_started", peer=context.peer())

        # Create a queue for this subscriber
        queue: asyncio.Queue[pb.TickEvent] = asyncio.Queue()
        self._subscribers.append(queue)

        try:
            while context.is_active():
                # Wait for next tick event
                try:
                    # Use a timeout to periodically check if context is still active
                    event = queue.get_nowait()
                    yield event
                except asyncio.QueueEmpty:
                    # Sleep briefly and continue
                    import time

                    time.sleep(0.01)
                    continue

        except Exception as e:
            logger.warning("tick_stream_error", error=str(e))
        finally:
            self._subscribers.remove(queue)
            logger.info("tick_stream_ended", peer=context.peer())

    def broadcast_tick(self, tick_event: pb.TickEvent) -> None:
        """Broadcast a tick event to all subscribers.

        Called by the server when a new tick starts.
        """
        for queue in self._subscribers:
            try:
                queue.put_nowait(tick_event)
            except asyncio.QueueFull:
                logger.warning("subscriber_queue_full")

    def create_tick_event(self) -> pb.TickEvent:
        """Create a TickEvent from the current tick loop state."""
        ctx = self.tick_loop.current_context
        if ctx is None:
            raise RuntimeError("No tick in progress")

        return pb.TickEvent(
            tick_id=ctx.tick_id,
            tick_start_server_time_ms=ctx.start_time_ms,
            intent_deadline_server_time_ms=ctx.deadline_ms,
            tick_duration_ms=self.tick_loop.config.tick_duration_ms,
            world_version=WORLD_VERSION,
        )
