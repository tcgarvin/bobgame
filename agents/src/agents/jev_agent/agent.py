"""The agent process: one tick loop, one planner task, one entity.

The tick loop owns the world connection and submits exactly one intent per tick,
before the deadline. The planner runs as a background task and reaches the tick
loop only through the handshakes on `JevAgent` - `run_stint`, `direct_action`
and `wait_ticks` - each of which parks the planner on an asyncio Future until
the tick loop has actually done the thing.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, TypeVar

import grpc
import structlog

from .. import world_pb2 as pb
from .client import WorldClient
from .jevclient import JevClient, TypeSafeJevClient
from .planner import Planner
from .stint import Brief, Stint, StintReport, default_log_path
from .worldmodel import TickDigest, WorldModel

logger = structlog.get_logger(__name__)

MODE_PLANNING = "planning"
MODE_STINT = "stint"
MODE_IDLE = "idle"

# Called with "accepted" or a rejection string once the world has answered.
ResultSink = Callable[[str], None]

_T = TypeVar("_T")

THOUGHT_CHANNEL = "thought"


@dataclass
class _StintRequest:
    """A planner `start_stint` waiting for the tick loop to pick it up."""

    brief: Brief
    future: asyncio.Future[StintReport]


@dataclass
class _DirectRequest:
    """A planner single-tick action waiting to be submitted and resolved."""

    intent: pb.Intent
    description: str
    future: asyncio.Future[str]
    remaining_ticks: int = 1
    results: list[str] = field(default_factory=list)


class JevAgent:
    """A planner and a Jev executor sharing one entity and one tick loop."""

    def __init__(
        self,
        world: WorldClient,
        jev: JevClient,
        entity_id: str,
        *,
        log_root: Path | None = None,
        planner_model: str = "",
    ) -> None:
        self.world = world
        self.jev = jev
        self.entity_id = entity_id
        self.log_root = Path("logs") if log_root is None else log_root
        self._model = WorldModel(entity_id)

        self.mode = MODE_IDLE
        self.planner = Planner(
            self, entity_id, model_name=planner_model, log_root=self.log_root
        )

        self._stint_requests: asyncio.Queue[_StintRequest] = asyncio.Queue()
        self._direct_requests: asyncio.Queue[_DirectRequest] = asyncio.Queue()
        self._active_stint: Stint | None = None
        self._active_request: _StintRequest | None = None
        self._awaiting_direct: _DirectRequest | None = None
        self._pending_thought = ""
        self._last_status = ("", "", "", "")
        self._running = False

    # -- AgentBridge --------------------------------------------------------

    @property
    def model(self) -> WorldModel:
        """The shared world model, updated at the top of every tick."""
        return self._model

    async def run_stint(self, brief: Brief) -> StintReport:
        """Queue a stint and wait for the tick loop to finish running it."""
        future: asyncio.Future[StintReport] = asyncio.get_running_loop().create_future()
        await self._stint_requests.put(_StintRequest(brief=brief, future=future))
        return await future

    async def direct_action(self, intent: pb.Intent, description: str) -> str:
        """Submit one intent on the next tick and report what the world did."""
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        await self._direct_requests.put(
            _DirectRequest(intent=intent, description=description, future=future)
        )
        return await future

    async def wait_ticks(self, ticks: int) -> str:
        """Hold position for `ticks` ticks."""
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        await self._direct_requests.put(
            _DirectRequest(
                intent=pb.Intent(wait=pb.WaitIntent()),
                description=f"wait {ticks} ticks",
                future=future,
                remaining_ticks=max(1, ticks),
            )
        )
        return await future

    def set_thought(self, thought: str) -> None:
        """Queue a planner reflection to be spoken on the `thought` channel."""
        self._pending_thought = thought

    # -- tick loop ----------------------------------------------------------

    async def run(self) -> None:
        """Connect, then drive the tick loop until the stream ends or we stop."""
        await self.world.acquire_lease()
        self._running = True
        self.mode = MODE_PLANNING

        renewal = asyncio.create_task(self.world.run_lease_renewal())
        planner_task = asyncio.create_task(self.planner.run())
        try:
            async for observation in self.world.observations():
                if not self._running:
                    break
                await self._handle_tick(observation)
        except grpc.RpcError as error:
            logger.error("observation_stream_failed", details=error.details())
        finally:
            self._running = False
            for task in (planner_task, renewal):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            self._fail_pending("agent stopped")

    def stop(self) -> None:
        """Ask the tick loop to finish after the current tick."""
        self._running = False

    async def _handle_tick(self, observation: pb.Observation) -> None:
        digest = self._model.update(observation)
        self._resolve_awaiting_direct(digest)

        intent, sink = await self._choose_intent(digest)

        if not self._model.self_info.alive:
            # The world rejects every intent from a dead entity; stay quiet
            # until the respawn instead of logging a rejection per tick.
            if sink is not None:
                sink("rejected: dead")
            await self._report_status()
            return

        try:
            result = await self.world.submit_intent(observation.tick_id, intent)
        except grpc.RpcError as error:
            logger.warning("submit_intent_rpc_error", details=error.details())
            result_text = f"rpc_error: {error.details()}"
        else:
            result_text = (
                "accepted" if result.accepted else f"rejected: {result.reason}"
            )
            if not result.accepted:
                logger.warning(
                    "intent_rejected", tick=observation.tick_id, reason=result.reason
                )

        if sink is not None:
            sink(result_text)

        await self._report_status()

    async def _choose_intent(
        self, digest: TickDigest
    ) -> tuple[pb.Intent, ResultSink | None]:
        """Pick this tick's intent and the callback that records its outcome."""
        stint = self._active_stint
        if stint is not None and not stint.finished:
            intent = await stint.decide(digest)
            if stint.finished:
                self._finish_stint()
                return intent, None
            return intent, stint.record_intent_result

        if stint is not None and stint.finished:
            self._finish_stint()

        if self._active_stint is None and not self._stint_requests.empty():
            stint = self._begin_stint(self._stint_requests.get_nowait())
            intent = await stint.decide(digest)
            if stint.finished:
                self._finish_stint()
                return intent, None
            return intent, stint.record_intent_result

        return self._planning_intent()

    def _planning_intent(self) -> tuple[pb.Intent, ResultSink | None]:
        if self._awaiting_direct is None and not self._direct_requests.empty():
            request = self._direct_requests.get_nowait()
            self._awaiting_direct = request
            return request.intent, None

        awaiting = self._awaiting_direct
        if awaiting is not None and awaiting.remaining_ticks > 1:
            awaiting.remaining_ticks -= 1
            return awaiting.intent, None

        if self._pending_thought:
            thought = self._pending_thought
            self._pending_thought = ""
            return (
                pb.Intent(
                    say=pb.SayIntent(text=thought[:400], channel=THOUGHT_CHANNEL)
                ),
                None,
            )

        return pb.Intent(wait=pb.WaitIntent()), None

    # -- stint / direct-action bookkeeping ----------------------------------

    def _begin_stint(self, request: _StintRequest) -> Stint:
        self._active_request = request
        stint = Stint(
            request.brief,
            self._model,
            self.jev,
            log_path=default_log_path(self.entity_id, self.log_root),
        )
        self._active_stint = stint
        self.mode = MODE_STINT
        logger.info("stint_started", brief=request.brief.instruction)
        return stint

    def _finish_stint(self) -> None:
        stint = self._active_stint
        request = self._active_request
        self._active_stint = None
        self._active_request = None
        self.mode = MODE_PLANNING
        if stint is None:
            return
        report = stint.build_report()
        self.planner.note_report(report)
        if request is not None and not request.future.done():
            request.future.set_result(report)

    def _resolve_awaiting_direct(self, digest: TickDigest) -> None:
        """Turn the world's own-action events into the planner's tool result."""
        request = self._awaiting_direct
        if request is None:
            return
        if request.remaining_ticks > 1:
            return
        outcome = "submitted"
        for acted in digest.own_actions:
            status = "ok" if acted.success else "failed"
            outcome = f"{acted.action_type} {status}: {acted.details or '(no detail)'}"
            break
        self._awaiting_direct = None
        if not request.future.done():
            request.future.set_result(f"{request.description} -> {outcome}")

    def _fail_pending(self, reason: str) -> None:
        for request in _drain(self._direct_requests):
            if not request.future.done():
                request.future.set_result(f"{request.description} -> {reason}")
        awaiting = self._awaiting_direct
        if awaiting is not None and not awaiting.future.done():
            awaiting.future.set_result(f"{awaiting.description} -> {reason}")
        self._awaiting_direct = None
        for stint_request in _drain(self._stint_requests):
            if not stint_request.future.done():
                stint_request.future.set_exception(RuntimeError(reason))

    # -- status -------------------------------------------------------------

    async def _report_status(self) -> None:
        stint = self._active_stint
        brief = stint.brief.summary() if stint is not None else ""
        stint_json = stint.status_json() if stint is not None else ""
        status = (self.mode, brief, self.planner.last_thought, stint_json)
        if status == self._last_status:
            return
        self._last_status = status
        try:
            await self.world.report_status(*status)
        except grpc.RpcError as error:
            logger.debug("status_report_failed", details=error.details())


def _drain(queue: "asyncio.Queue[_T]") -> list[_T]:
    items: list[_T] = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


async def run_agent(
    server_address: str,
    entity_id: str,
    *,
    log_root: Path | None = None,
    planner_model: str = "",
    jev_model: str = "",
) -> None:
    """Build every piece and run one actor until it is interrupted."""
    import os

    world = WorldClient(server_address, entity_id)
    jev = TypeSafeJevClient(jev_model or os.environ.get("JEV_MODEL", "jev-latest"))
    agent = JevAgent(
        world, jev, entity_id, log_root=log_root, planner_model=planner_model
    )
    try:
        await agent.run()
    finally:
        await jev.aclose()
        await world.close()
