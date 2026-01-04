"""WebSocket service for streaming viewer events to browser clients."""

import asyncio
import json
from typing import Any

import structlog
import websockets
from websockets import ConnectionClosed
from websockets.asyncio.server import Server, ServerConnection

from ..movement import MoveResult
from ..state import World
from ..tick import TickConfig, TickContext, TickResult

logger = structlog.get_logger()


class ViewerWebSocketService:
    """
    WebSocket service for streaming viewer events to browser clients.

    Manages WebSocket connections and broadcasts world events as JSON.
    Clients receive a snapshot on connect, then streaming tick events.
    """

    def __init__(
        self,
        world: World,
        tick_config: TickConfig,
        host: str = "0.0.0.0",
        port: int = 8765,
    ):
        self.world = world
        self.tick_config = tick_config
        self.host = host
        self.port = port
        self._clients: set[ServerConnection] = set()
        self._server: Server | None = None
        self._broadcast_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._broadcast_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the WebSocket server."""
        self._server = await websockets.serve(
            self._handle_client,
            self.host,
            self.port,
        )
        self._broadcast_task = asyncio.create_task(self._broadcast_loop())
        logger.info("viewer_ws_started", host=self.host, port=self.port)

    async def stop(self) -> None:
        """Stop the WebSocket server and close all connections."""
        if self._broadcast_task:
            self._broadcast_task.cancel()
            try:
                await self._broadcast_task
            except asyncio.CancelledError:
                pass

        if self._server:
            self._server.close()
            await self._server.wait_closed()
            logger.info("viewer_ws_stopped")

        # Close all client connections
        for client in list(self._clients):
            try:
                await client.close()
            except Exception:
                pass
        self._clients.clear()

    async def _handle_client(self, websocket: ServerConnection) -> None:
        """Handle a new client connection. Send snapshot, then keep alive."""
        self._clients.add(websocket)
        client_id = id(websocket)
        logger.info("viewer_client_connected", client_id=client_id)

        try:
            # Send initial snapshot
            snapshot = self._generate_snapshot()
            await websocket.send(json.dumps(snapshot))
            logger.debug("snapshot_sent", client_id=client_id, tick_id=snapshot["tick_id"])

            # Keep connection alive, handle incoming messages (if any)
            async for message in websocket:
                # Currently we don't expect any client messages,
                # but we consume them to keep the connection alive
                logger.debug("viewer_message_received", client_id=client_id, message=message)

        except ConnectionClosed:
            logger.debug("viewer_client_disconnected", client_id=client_id)
        except Exception as e:
            logger.warning("viewer_client_error", client_id=client_id, error=str(e))
        finally:
            self._clients.discard(websocket)

    async def _broadcast_loop(self) -> None:
        """Background task that broadcasts events from the queue."""
        while True:
            event = await self._broadcast_queue.get()
            await self._broadcast_event(event)

    async def _broadcast_event(self, event: dict[str, Any]) -> None:
        """Broadcast an event to all connected clients."""
        if not self._clients:
            return

        message = json.dumps(event)
        disconnected: list[ServerConnection] = []

        # Send to all clients, tracking failures
        for client in self._clients:
            try:
                await client.send(message)
            except ConnectionClosed:
                disconnected.append(client)
            except Exception as e:
                logger.warning("broadcast_error", error=str(e))
                disconnected.append(client)

        # Remove disconnected clients
        for client in disconnected:
            self._clients.discard(client)

    def broadcast_event(self, event: dict[str, Any]) -> None:
        """Queue an event for broadcast (non-blocking)."""
        try:
            self._broadcast_queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("broadcast_queue_full")

    def _generate_snapshot(self) -> dict[str, Any]:
        """Generate current world state snapshot."""
        entities = []
        for entity in self.world.all_entities().values():
            entities.append({
                "entity_id": entity.entity_id,
                "position": {"x": entity.position.x, "y": entity.position.y},
                "entity_type": entity.entity_type,
                "tags": list(entity.tags),
            })

        objects = []
        for obj in self.world.all_objects().values():
            objects.append({
                "object_id": obj.object_id,
                "position": {"x": obj.position.x, "y": obj.position.y},
                "object_type": obj.object_type,
                "state": dict(obj.state),
            })

        return {
            "type": "snapshot",
            "tick_id": self.world.tick,
            "entities": entities,
            "objects": objects,
            "world_size": {"width": self.world.width, "height": self.world.height},
            "tick_duration_ms": self.tick_config.tick_duration_ms,
        }

    def on_tick_start(self, context: TickContext) -> None:
        """Called at tick start - broadcasts tick_started event."""
        event = {
            "type": "tick_started",
            "tick_id": context.tick_id,
            "tick_start_ms": context.start_time_ms,
            "deadline_ms": context.deadline_ms,
            "tick_duration_ms": self.tick_config.tick_duration_ms,
        }
        self.broadcast_event(event)

    def on_tick_complete(self, result: TickResult) -> None:
        """Called after tick processing - broadcasts tick_completed with move results."""
        moves = []
        for move_result in result.move_results:
            moves.append({
                "entity_id": move_result.entity_id,
                "from": {"x": move_result.from_pos.x, "y": move_result.from_pos.y},
                "to": {"x": move_result.to_pos.x, "y": move_result.to_pos.y},
                "success": move_result.success,
            })

        object_changes = []
        for change in result.object_changes:
            object_changes.append({
                "object_id": change.object_id,
                "field": change.field,
                "old_value": change.old_value,
                "new_value": change.new_value,
            })

        total_actions = (
            len(result.move_results)
            + len(result.collect_results)
            + len(result.eat_results)
        )

        event = {
            "type": "tick_completed",
            "tick_id": result.tick_id,
            "moves": moves,
            "object_changes": object_changes,
            "actions_processed": total_actions,
        }
        self.broadcast_event(event)

    @property
    def client_count(self) -> int:
        """Number of connected clients."""
        return len(self._clients)
