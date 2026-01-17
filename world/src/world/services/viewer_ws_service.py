"""WebSocket service for streaming viewer events to browser clients."""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import structlog
import websockets
from websockets import ConnectionClosed
from websockets.asyncio.server import Server, ServerConnection

from ..chunks import CHUNK_SIZE, ChunkManager
from ..encoding import encode_terrain_base64
from ..state import World
from ..tick import TickConfig, TickContext, TickResult

logger = structlog.get_logger()


@dataclass
class ViewerClientState:
    """Per-client subscription state for chunk-based streaming."""

    subscribed_chunks: set[tuple[int, int]] = field(default_factory=set)
    chunk_versions: dict[tuple[int, int], int] = field(default_factory=dict)


class ViewerWebSocketService:
    """
    WebSocket service for streaming viewer events to browser clients.

    Manages WebSocket connections and broadcasts world events as JSON.
    Clients receive a snapshot on connect, then subscribe to chunks for
    terrain and filtered entity/object updates.
    """

    def __init__(
        self,
        world: World,
        tick_config: TickConfig,
        host: str = "0.0.0.0",
        port: int = 8765,
        chunk_manager: ChunkManager | None = None,
    ):
        self.world = world
        self.tick_config = tick_config
        self.host = host
        self.port = port
        self._clients: set[ServerConnection] = set()
        self._client_states: dict[int, ViewerClientState] = {}
        self._server: Server | None = None
        self._broadcast_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._broadcast_task: asyncio.Task[None] | None = None

        # Use provided chunk manager or create one
        if chunk_manager is not None:
            self._chunk_manager = chunk_manager
        else:
            self._chunk_manager = ChunkManager(world)
            self._chunk_manager.initialize_from_world()

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
        self._client_states.clear()

    async def _handle_client(self, websocket: ServerConnection) -> None:
        """Handle a new client connection. Send snapshot, then process subscriptions."""
        self._clients.add(websocket)
        client_id = id(websocket)
        self._client_states[client_id] = ViewerClientState()
        logger.info("viewer_client_connected", client_id=client_id)

        try:
            # Send initial snapshot (metadata only, no terrain)
            snapshot = self._generate_snapshot()
            await websocket.send(json.dumps(snapshot))
            logger.debug(
                "snapshot_sent", client_id=client_id, tick_id=snapshot["tick_id"]
            )

            # Process incoming messages (subscriptions)
            async for message in websocket:
                await self._handle_message(client_id, websocket, message)

        except ConnectionClosed:
            logger.debug("viewer_client_disconnected", client_id=client_id)
        except Exception as e:
            logger.warning("viewer_client_error", client_id=client_id, error=str(e))
        finally:
            self._clients.discard(websocket)
            self._client_states.pop(client_id, None)

    async def _handle_message(
        self, client_id: int, websocket: ServerConnection, raw_message: str
    ) -> None:
        """Handle an incoming client message."""
        try:
            message = json.loads(raw_message)
        except json.JSONDecodeError:
            logger.warning("invalid_json", client_id=client_id)
            return

        msg_type = message.get("type")

        if msg_type == "subscribe_viewport":
            await self._handle_subscribe_viewport(client_id, websocket, message)
        elif msg_type == "subscribe_chunks":
            await self._handle_subscribe_chunks(client_id, websocket, message)
        else:
            logger.debug(
                "viewer_message_received",
                client_id=client_id,
                type=msg_type,
            )

    async def _handle_subscribe_viewport(
        self, client_id: int, websocket: ServerConnection, message: dict[str, Any]
    ) -> None:
        """Handle viewport subscription - subscribe to chunks covering viewport."""
        viewport = message.get("viewport", {})
        x = viewport.get("x", 0)
        y = viewport.get("y", 0)
        width = viewport.get("width", 64)
        height = viewport.get("height", 48)

        chunks = self._chunk_manager.get_chunks_for_viewport(x, y, width, height)
        await self._subscribe_to_chunks(client_id, websocket, chunks)

    async def _handle_subscribe_chunks(
        self, client_id: int, websocket: ServerConnection, message: dict[str, Any]
    ) -> None:
        """Handle explicit chunk subscription."""
        chunk_list = message.get("chunks", [])
        chunks = [(int(c[0]), int(c[1])) for c in chunk_list if len(c) >= 2]
        await self._subscribe_to_chunks(client_id, websocket, chunks)

    async def _subscribe_to_chunks(
        self,
        client_id: int,
        websocket: ServerConnection,
        chunks: list[tuple[int, int]],
    ) -> None:
        """Subscribe client to specified chunks, sending data for new ones."""
        client_state = self._client_states.get(client_id)
        if not client_state:
            return

        new_chunks = set(chunks) - client_state.subscribed_chunks
        old_chunks = client_state.subscribed_chunks - set(chunks)

        # Send unload messages for chunks no longer needed
        for chunk_x, chunk_y in old_chunks:
            await self._send_to_client(
                websocket,
                {"type": "chunk_unload", "chunk_x": chunk_x, "chunk_y": chunk_y},
            )

        # Update subscription
        client_state.subscribed_chunks = set(chunks)

        # Send chunk data for newly subscribed chunks
        for chunk_x, chunk_y in new_chunks:
            chunk = self._chunk_manager.get_chunk(chunk_x, chunk_y)
            if chunk:
                await self._send_chunk_data(websocket, chunk)
                client_state.chunk_versions[(chunk_x, chunk_y)] = chunk.version

        logger.debug(
            "chunks_subscribed",
            client_id=client_id,
            count=len(chunks),
            new=len(new_chunks),
            removed=len(old_chunks),
        )

    async def _send_chunk_data(
        self, websocket: ServerConnection, chunk: Any
    ) -> None:
        """Send full chunk data to a client."""
        # Get entities in this chunk
        entities = []
        for entity_id in chunk.entities:
            try:
                entity = self.world.get_entity(entity_id)
                entities.append(
                    {
                        "entity_id": entity.entity_id,
                        "position": {"x": entity.position.x, "y": entity.position.y},
                        "entity_type": entity.entity_type,
                        "tags": list(entity.tags),
                    }
                )
            except Exception:
                pass  # Entity may have been removed

        # Get objects in this chunk
        objects = []
        for object_id in chunk.objects:
            try:
                obj = self.world.get_object(object_id)
                objects.append(
                    {
                        "object_id": obj.object_id,
                        "position": {"x": obj.position.x, "y": obj.position.y},
                        "object_type": obj.object_type,
                        "state": dict(obj.state),
                    }
                )
            except Exception:
                pass  # Object may have been removed

        message = {
            "type": "chunk_data",
            "chunk_x": chunk.chunk_x,
            "chunk_y": chunk.chunk_y,
            "version": chunk.version,
            "terrain": encode_terrain_base64(chunk.terrain),
            "entities": entities,
            "objects": objects,
        }
        await self._send_to_client(websocket, message)

    async def _send_to_client(
        self, websocket: ServerConnection, message: dict[str, Any]
    ) -> None:
        """Send a message to a specific client."""
        try:
            await websocket.send(json.dumps(message))
        except ConnectionClosed:
            pass
        except Exception as e:
            logger.warning("send_error", error=str(e))

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
            self._client_states.pop(id(client), None)

    def broadcast_event(self, event: dict[str, Any]) -> None:
        """Queue an event for broadcast (non-blocking)."""
        try:
            self._broadcast_queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("broadcast_queue_full")

    def _generate_snapshot(self) -> dict[str, Any]:
        """Generate world metadata snapshot (no terrain, clients subscribe to chunks)."""
        return {
            "type": "snapshot",
            "tick_id": self.world.tick,
            "world_size": {"width": self.world.width, "height": self.world.height},
            "chunk_size": CHUNK_SIZE,
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
            moves.append(
                {
                    "entity_id": move_result.entity_id,
                    "from": {"x": move_result.from_pos.x, "y": move_result.from_pos.y},
                    "to": {"x": move_result.to_pos.x, "y": move_result.to_pos.y},
                    "success": move_result.success,
                }
            )

            # Update chunk manager for entity movements
            if move_result.success:
                self._chunk_manager.update_entity_position(
                    move_result.entity_id,
                    move_result.from_pos,
                    move_result.to_pos,
                )

        object_changes = []
        for change in result.object_changes:
            object_changes.append(
                {
                    "object_id": change.object_id,
                    "field": change.field,
                    "old_value": change.old_value,
                    "new_value": change.new_value,
                }
            )

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

    @property
    def chunk_manager(self) -> ChunkManager:
        """Access the chunk manager."""
        return self._chunk_manager
