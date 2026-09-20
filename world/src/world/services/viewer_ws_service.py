"""WebSocket service for streaming viewer events to browser clients."""

import asyncio
import json
from typing import Any

import structlog
import websockets
from websockets import ConnectionClosed
from websockets.asyncio.server import Server, ServerConnection

from ..chunks import CHUNK_SIZE, ChunkManager
from ..exceptions import EntityNotFoundError
from ..state import World
from ..tick import TickConfig, TickContext, TickResult
from ..types import Position
from ..viewer_payload import (
    action_payload,
    entity_state,
    clock_payload,
    entity_updates,
    move_payload,
    object_change_payload,
    object_state,
    utterance_payload,
)
from .chunk_subscriptions import (
    ChunkSubscription,
    apply_subscription,
    chunk_data_message,
    requested_chunks,
    viewport_chunks,
)


# Action types counted in `actions_processed` beside the moves.
FORAGING = frozenset({"collect", "eat"})
logger = structlog.get_logger()


# Per-client subscription state for chunk-based streaming.
ViewerClientState = ChunkSubscription


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
        run_id: str = "",
    ):
        self.world = world
        self.tick_config = tick_config
        self.run_id = run_id
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
            except (OSError, websockets.exceptions.WebSocketException) as exc:
                # A viewer that has already gone is nothing to report at
                # shutdown, but it is worth seeing in the log.
                logger.debug("viewer_ws_close_failed", error=str(exc))
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
        chunks = viewport_chunks(self._chunk_manager, message)
        await self._subscribe_to_chunks(client_id, websocket, chunks)

    async def _handle_subscribe_chunks(
        self, client_id: int, websocket: ServerConnection, message: dict[str, Any]
    ) -> None:
        """Handle explicit chunk subscription."""
        await self._subscribe_to_chunks(client_id, websocket, requested_chunks(message))

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

        async def send(message: dict[str, Any]) -> None:
            await self._send_to_client(websocket, message)

        added, removed = await apply_subscription(
            client_state, self._chunk_manager, self.world, chunks, send
        )

        logger.debug(
            "chunks_subscribed",
            client_id=client_id,
            count=len(chunks),
            new=added,
            removed=removed,
        )

    async def _send_chunk_data(self, websocket: ServerConnection, chunk: Any) -> None:
        """Send full chunk data to a client."""
        await self._send_to_client(websocket, chunk_data_message(chunk, self.world))

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
        settlement = self.world.settlement
        return {
            "type": "snapshot",
            "tick_id": self.world.tick,
            "clock": clock_payload(self.world.clock),
            "world_size": {"width": self.world.width, "height": self.world.height},
            "chunk_size": CHUNK_SIZE,
            "tick_duration_ms": self.tick_config.tick_duration_ms,
            "settlement": (
                {"x": settlement.x, "y": settlement.y}
                if settlement is not None
                else None
            ),
            "run_id": self.run_id or None,
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
        moves = [move_payload(move_result) for move_result in result.move_results]
        for move_result in result.move_results:
            # Update chunk manager for entity movements
            if move_result.success:
                self._chunk_manager.update_entity_position(
                    move_result.entity_id,
                    move_result.from_pos,
                    move_result.to_pos,
                )

        object_changes = [
            object_change_payload(change) for change in result.object_changes
        ]

        # Objects added/removed this tick, kept in sync with the chunk index.
        objects_added = []
        for added in result.objects_added:
            self._chunk_manager.add_object(added.obj.object_id, added.obj.position)
            objects_added.append(object_state(added.obj))

        objects_removed = []
        for removed in result.objects_removed:
            self._chunk_manager.remove_object(removed.object_id)
            objects_removed.append(removed.object_id)

        actions = [action_payload(action) for action in result.action_results]

        utterances = [utterance_payload(utterance) for utterance in result.utterances]

        # Moves plus the two foraging actions, as this count has always meant.
        total_actions = len(result.move_results) + sum(
            1 for action in result.action_results if action.action_type in FORAGING
        )

        event = {
            "type": "tick_completed",
            "tick_id": result.tick_id,
            "clock": clock_payload(self.world.clock),
            "moves": moves,
            "object_changes": object_changes,
            "actions_processed": total_actions,
            "entity_updates": self._entity_updates(),
            "actions": actions,
            "utterances": utterances,
            "objects_added": objects_added,
            "objects_removed": objects_removed,
        }
        self.broadcast_event(event)

        self._emit_spawns_and_despawns(result)

    def _entity_updates(self) -> list[dict[str, Any]]:
        """Full state of every entity, sent every tick."""
        return entity_updates(self.world)

    def _emit_spawns_and_despawns(self, result: TickResult) -> None:
        """Emit spawn/despawn messages and keep the chunk index in sync."""
        for spawned in result.entities_spawned:
            self._chunk_manager.sync_entity_position(
                spawned.entity_id, spawned.position
            )
            self.broadcast_event(
                {
                    "type": "entity_spawned",
                    "tick_id": result.tick_id,
                    "entity": self._entity_state_by_id(
                        spawned.entity_id, spawned.position, spawned.entity_type
                    ),
                }
            )

        for respawned in result.respawns:
            self._chunk_manager.sync_entity_position(
                respawned.entity_id, respawned.position
            )
            self.broadcast_event(
                {
                    "type": "entity_spawned",
                    "tick_id": result.tick_id,
                    "entity": self._entity_state_by_id(
                        respawned.entity_id, respawned.position, "player"
                    ),
                }
            )

        for despawned in result.entities_despawned:
            self._chunk_manager.remove_entity(despawned.entity_id)
            self.broadcast_event(
                {
                    "type": "entity_despawned",
                    "tick_id": result.tick_id,
                    "entity_id": despawned.entity_id,
                    "reason": despawned.reason,
                }
            )

    def _entity_state_by_id(
        self, entity_id: str, position: Position, entity_type: str
    ) -> dict[str, Any]:
        """Entity state for a spawn message, falling back to the event data."""
        try:
            return entity_state(self.world.get_entity(entity_id))
        except EntityNotFoundError:
            return {
                "entity_id": entity_id,
                "position": {"x": position.x, "y": position.y},
                "entity_type": entity_type,
                "tags": [],
                "health": 0,
                "max_health": 0,
                "food": 0,
                "max_food": 0,
                "wielded": "",
                "alive": False,
                "inventory": {},
            }

    @property
    def client_count(self) -> int:
        """Number of connected clients."""
        return len(self._clients)

    @property
    def chunk_manager(self) -> ChunkManager:
        """Access the chunk manager."""
        return self._chunk_manager
