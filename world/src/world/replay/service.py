"""WebSocket server that replays recorded runs to the viewer.

Speaks the live viewer protocol (`snapshot`, `chunk_data`, `tick_started`,
`tick_completed`, `entity_spawned`/`entity_despawned`, `agent_status`) plus the
replay control messages in docs/07_replay.md.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
import websockets
from websockets import ConnectionClosed
from websockets.asyncio.server import Server, ServerConnection

from ..services.chunk_subscriptions import (
    ChunkSubscription,
    apply_subscription,
    requested_chunks,
    resend_subscribed_chunks,
    viewport_chunks,
)
from .loader import RunLoadError, RunLoader
from .session import ReplaySession

logger = structlog.get_logger()

DEFAULT_REPLAY_PORT = 8766

# Playback never sleeps less than this between ticks.
MIN_FRAME_S = 0.01


@dataclass
class ReplayClient:
    """Per-connection state: one run session and its chunk subscription."""

    connection: ServerConnection
    subscription: ChunkSubscription = field(default_factory=ChunkSubscription)
    session: ReplaySession | None = None
    play_task: asyncio.Task[None] | None = None


class ReplayWebSocketService:
    """Serves every run under `runs_dir` to viewer clients."""

    def __init__(
        self,
        runs_dir: Path,
        project_root: Path,
        host: str = "0.0.0.0",
        port: int = DEFAULT_REPLAY_PORT,
    ):
        self.runs_dir = runs_dir
        self.project_root = project_root
        self.host = host
        self.port = port
        self._server: Server | None = None
        self._clients: dict[int, ReplayClient] = {}
        self._loaders: dict[str, RunLoader] = {}

    # --- lifecycle ---

    async def start(self) -> None:
        """Start the WebSocket server."""
        self._server = await websockets.serve(self._handle_client, self.host, self.port)
        logger.info(
            "replay_ws_started",
            host=self.host,
            port=self.port,
            runs_dir=str(self.runs_dir),
        )

    async def stop(self) -> None:
        """Stop the server and close every connection."""
        for client in list(self._clients.values()):
            self._stop_playback(client)
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            logger.info("replay_ws_stopped")
        self._clients.clear()

    async def serve_forever(self) -> None:
        """Start and run until cancelled."""
        await self.start()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    # --- run loading ---

    def load_run(self, run_id: str) -> RunLoader:
        """Load (and cache) a run by id.

        Raises:
            RunLoadError: If the run directory is missing or unusable.
        """
        cached = self._loaders.get(run_id)
        if cached is not None:
            return cached
        if "/" in run_id or run_id in ("", ".", ".."):
            raise RunLoadError(f"Invalid run id: {run_id!r}")
        run_dir = self.runs_dir / run_id
        if not run_dir.is_dir():
            raise RunLoadError(f"No run directory for {run_id!r}")
        loader = RunLoader(run_dir.resolve())
        self._loaders[run_id] = loader
        logger.info(
            "run_loaded",
            run_id=run_id,
            ticks=len(loader.tick_ids),
            agents=len(loader.agents),
        )
        return loader

    def list_runs(self) -> list[str]:
        """Run ids available under the runs directory, newest first."""
        if not self.runs_dir.is_dir():
            return []
        names = [
            entry.name
            for entry in self.runs_dir.iterdir()
            if entry.is_dir() and (entry / "meta.json").exists()
        ]
        return sorted(names, reverse=True)

    # --- connection handling ---

    async def _handle_client(self, websocket: ServerConnection) -> None:
        client = ReplayClient(connection=websocket)
        client_id = id(websocket)
        self._clients[client_id] = client
        logger.info("replay_client_connected", client_id=client_id)
        try:
            async for message in websocket:
                await self._handle_message(client, message)
        except ConnectionClosed:
            logger.debug("replay_client_disconnected", client_id=client_id)
        finally:
            self._stop_playback(client)
            self._clients.pop(client_id, None)

    async def _handle_message(self, client: ReplayClient, raw: str | bytes) -> None:
        """Dispatch one client message."""
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            await self._error(client, "invalid JSON")
            return
        if not isinstance(message, dict):
            await self._error(client, "message must be an object")
            return

        handlers = {
            "open_run": self._handle_open_run,
            "subscribe_viewport": self._handle_subscribe_viewport,
            "subscribe_chunks": self._handle_subscribe_chunks,
            "seek": self._handle_seek,
            "step": self._handle_step,
            "play": self._handle_play,
            "pause": self._handle_pause,
            "get_agent_detail": self._handle_get_agent_detail,
            "get_run_index": self._handle_get_run_index,
        }
        handler = handlers.get(str(message.get("type", "")))
        if handler is None:
            await self._error(client, f"unknown message type: {message.get('type')}")
            return
        await handler(client, message)

    # --- handlers ---

    async def _handle_open_run(
        self, client: ReplayClient, message: dict[str, Any]
    ) -> None:
        run_id = str(message.get("run_id", ""))
        self._stop_playback(client)
        try:
            loader = self.load_run(run_id)
        except RunLoadError as exc:
            await self._error(client, str(exc))
            return
        try:
            client.session = ReplaySession(loader, self.project_root)
        except (OSError, ValueError, KeyError) as exc:
            logger.warning("replay_session_failed", run_id=run_id, error=str(exc))
            await self._error(client, f"could not open {run_id}: {exc}")
            return

        await self._send_position(client, include_run_index=True)

    async def _handle_subscribe_viewport(
        self, client: ReplayClient, message: dict[str, Any]
    ) -> None:
        session = client.session
        if session is None:
            await self._error(client, "no run open")
            return
        await self._subscribe(client, viewport_chunks(session.chunk_manager, message))

    async def _handle_subscribe_chunks(
        self, client: ReplayClient, message: dict[str, Any]
    ) -> None:
        if client.session is None:
            await self._error(client, "no run open")
            return
        await self._subscribe(client, requested_chunks(message))

    async def _handle_seek(self, client: ReplayClient, message: dict[str, Any]) -> None:
        session = client.session
        if session is None:
            await self._error(client, "no run open")
            return
        try:
            target = int(message.get("tick_id", session.tick_id))
        except (TypeError, ValueError):
            await self._error(client, "seek needs an integer tick_id")
            return
        self._stop_playback(client)
        session.seek(target)
        await self._send_position(client)

    async def _handle_step(self, client: ReplayClient, message: dict[str, Any]) -> None:
        session = client.session
        if session is None:
            await self._error(client, "no run open")
            return
        try:
            delta = int(message.get("delta", 1))
        except (TypeError, ValueError):
            await self._error(client, "step needs an integer delta")
            return
        self._stop_playback(client)
        session.step(delta)
        await self._send_position(client)

    async def _handle_play(self, client: ReplayClient, message: dict[str, Any]) -> None:
        session = client.session
        if session is None:
            await self._error(client, "no run open")
            return
        try:
            speed = float(message.get("speed", 1))
        except (TypeError, ValueError):
            await self._error(client, "play needs a numeric speed")
            return
        if speed <= 0:
            await self._error(client, "speed must be greater than 0")
            return

        self._stop_playback(client)
        session.speed = speed
        if session.at_end():
            session.playing = False
            await self._send(client, session.replay_status())
            return
        session.playing = True
        await self._send(client, session.replay_status())
        client.play_task = asyncio.create_task(self._playback_loop(client))

    async def _handle_pause(
        self, client: ReplayClient, message: dict[str, Any]
    ) -> None:
        session = client.session
        if session is None:
            await self._error(client, "no run open")
            return
        self._stop_playback(client)
        await self._send(client, session.replay_status())

    async def _handle_get_agent_detail(
        self, client: ReplayClient, message: dict[str, Any]
    ) -> None:
        session = client.session
        if session is None:
            await self._error(client, "no run open")
            return
        entity_id = str(message.get("entity_id", ""))
        try:
            tick_id = int(message.get("tick_id", session.tick_id))
        except (TypeError, ValueError):
            await self._error(client, "get_agent_detail needs an integer tick_id")
            return
        detail = session.loader.agent_detail(entity_id, tick_id)
        detail.update(
            {"type": "agent_detail", "entity_id": entity_id, "tick_id": tick_id}
        )
        await self._send(client, detail)

    async def _handle_get_run_index(
        self, client: ReplayClient, message: dict[str, Any]
    ) -> None:
        session = client.session
        if session is None:
            await self._error(client, "no run open")
            return
        await self._send(client, session.loader.run_index())

    # --- playback ---

    async def _playback_loop(self, client: ReplayClient) -> None:
        """Advance one tick at a time until the end of the run."""
        session = client.session
        if session is None:
            return
        try:
            while session.playing and not session.at_end():
                delay = session.loader.tick_duration_ms / 1000.0 / session.speed
                await asyncio.sleep(max(MIN_FRAME_S, delay))
                if not session.playing:
                    return
                target = session.step(1)
                await self._send(client, session.tick_started(target))
                await self._send(client, session.tick_completed(target))
                for spawn in session.spawn_messages(target):
                    await self._send(client, spawn)
                for status in session.agent_status_at(target):
                    await self._send(client, status)
                await self._send(client, session.replay_status())
            session.playing = False
            client.play_task = None
            await self._send(client, session.replay_status())
        except asyncio.CancelledError:
            raise
        except ConnectionClosed:
            session.playing = False

    def _stop_playback(self, client: ReplayClient) -> None:
        """Cancel any running playback task and clear the playing flag."""
        if client.session is not None:
            client.session.playing = False
        task = client.play_task
        client.play_task = None
        if task is not None and not task.done():
            task.cancel()

    # --- sending ---

    async def _send_position(
        self, client: ReplayClient, include_run_index: bool = False
    ) -> None:
        """Send the ordered burst after open_run / seek / step."""
        session = client.session
        if session is None:
            return
        await self._send(client, session.snapshot())
        if include_run_index:
            await self._send(client, session.loader.run_index())

        async def send(message: dict[str, Any]) -> None:
            await self._send(client, message)

        await resend_subscribed_chunks(
            client.subscription, session.chunk_manager, session.world, send
        )
        await self._send(client, session.tick_completed(session.tick_id))
        await self._send(client, session.entity_log(session.tick_id))
        for status in session.agent_status_messages(session.tick_id):
            await self._send(client, status)
        await self._send(client, session.replay_status())

    async def _subscribe(
        self, client: ReplayClient, chunks: list[tuple[int, int]]
    ) -> None:
        session = client.session
        if session is None:
            return

        async def send(message: dict[str, Any]) -> None:
            await self._send(client, message)

        await apply_subscription(
            client.subscription,
            session.chunk_manager,
            session.world,
            chunks,
            send,
        )

    async def _send(self, client: ReplayClient, message: dict[str, Any]) -> None:
        """Send one message, ignoring a closed connection."""
        try:
            await client.connection.send(json.dumps(message))
        except ConnectionClosed:
            pass

    async def _error(self, client: ReplayClient, text: str) -> None:
        logger.warning("replay_error", message=text)
        await self._send(client, {"type": "error", "message": text})
