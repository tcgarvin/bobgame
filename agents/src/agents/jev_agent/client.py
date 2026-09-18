"""Asyncio-friendly wrapper around the world server's sync gRPC stubs.

The generated stubs are blocking, so the observation stream runs on a worker
thread that pushes into an `asyncio.Queue`, and the unary calls go through
`asyncio.to_thread`. Nothing in the agent's event loop ever blocks on gRPC.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import AsyncIterator, Iterator, cast

import grpc
import structlog

from .. import world_pb2 as pb
from .. import world_pb2_grpc

logger = structlog.get_logger(__name__)

LEASE_RENEW_SECONDS = 10.0
OBSERVATION_QUEUE_SIZE = 8


class WorldConnectionError(RuntimeError):
    """Raised when the agent cannot obtain or keep a usable connection."""


@dataclass(frozen=True)
class IntentResult:
    """The world server's verdict on a submitted intent."""

    accepted: bool
    reason: str


class WorldClient:
    """Lease, observations, intents, and status reporting for one entity."""

    def __init__(
        self,
        server_address: str,
        entity_id: str,
        controller_id: str = "",
    ) -> None:
        self.server_address = server_address
        self.entity_id = entity_id
        self.controller_id = controller_id or f"jev-agent-{entity_id}"

        self._channel = grpc.insecure_channel(server_address)
        self._lease_stub = world_pb2_grpc.LeaseServiceStub(self._channel)
        self._action_stub = world_pb2_grpc.ActionServiceStub(self._channel)
        self._observation_stub = world_pb2_grpc.ObservationServiceStub(self._channel)
        self._status_stub = world_pb2_grpc.AgentStatusServiceStub(self._channel)

        self.lease_id = ""
        self._stream_thread: threading.Thread = threading.Thread(target=lambda: None)
        self._stop = threading.Event()
        self._stream_call: grpc.RpcContext | None = None

    # -- lifecycle ----------------------------------------------------------

    async def acquire_lease(self) -> None:
        """Acquire the control lease, raising `WorldConnectionError` on refusal."""
        response = await asyncio.to_thread(
            self._lease_stub.AcquireLease,
            pb.AcquireLeaseRequest(
                entity_id=self.entity_id, controller_id=self.controller_id
            ),
        )
        if not response.success:
            raise WorldConnectionError(
                f"lease refused for {self.entity_id}: {response.reason}"
            )
        self.lease_id = response.lease_id
        logger.info("lease_acquired", entity_id=self.entity_id, lease=self.lease_id)

    async def renew_lease(self) -> bool:
        """Renew the lease; False when the server refused."""
        response = await asyncio.to_thread(
            self._lease_stub.RenewLease, pb.RenewLeaseRequest(lease_id=self.lease_id)
        )
        if not response.success:
            logger.warning("lease_renewal_failed", reason=response.reason)
        return bool(response.success)

    async def run_lease_renewal(self) -> None:
        """Renew the lease every `LEASE_RENEW_SECONDS` until cancelled."""
        while True:
            await asyncio.sleep(LEASE_RENEW_SECONDS)
            try:
                await self.renew_lease()
            except grpc.RpcError as error:
                logger.warning("lease_renewal_rpc_error", details=error.details())

    async def close(self) -> None:
        """Release the lease and tear the channel down."""
        self._stop.set()
        if self._stream_call is not None:
            self._stream_call.cancel()
        if self.lease_id:
            try:
                await asyncio.to_thread(
                    self._lease_stub.ReleaseLease,
                    pb.ReleaseLeaseRequest(lease_id=self.lease_id),
                )
            except grpc.RpcError as error:
                logger.warning("lease_release_failed", details=error.details())
            self.lease_id = ""
        self._channel.close()

    # -- observations -------------------------------------------------------

    async def observations(self) -> AsyncIterator[pb.Observation]:
        """Yield observations as the world server produces them.

        The blocking stream is drained on a worker thread; the iterator ends
        when the server closes the stream or `close()` is called.
        """
        if not self.lease_id:
            raise WorldConnectionError("observations() requires a lease")

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[pb.Observation | BaseException | None] = asyncio.Queue(
            maxsize=OBSERVATION_QUEUE_SIZE
        )

        def pump() -> None:
            try:
                stream: Iterator[pb.Observation] = (
                    self._observation_stub.StreamObservations(
                        pb.StreamObservationsRequest(
                            lease_id=self.lease_id, entity_id=self.entity_id
                        )
                    )
                )
                # The generated stub returns a call object that is also an
                # iterator; we keep it only to cancel the stream on shutdown.
                self._stream_call = cast(grpc.RpcContext, stream)
                for observation in stream:
                    if self._stop.is_set():
                        break
                    asyncio.run_coroutine_threadsafe(queue.put(observation), loop)
            except grpc.RpcError as error:
                asyncio.run_coroutine_threadsafe(queue.put(error), loop)
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(None), loop)

        self._stream_thread = threading.Thread(
            target=pump, name=f"obs-{self.entity_id}", daemon=True
        )
        self._stream_thread.start()

        while True:
            item = await queue.get()
            # If the loop fell behind (a slow API call), skip straight to the
            # newest observation: intents for old ticks are rejected anyway.
            skipped = 0
            while isinstance(item, pb.Observation) and not queue.empty():
                newer = queue.get_nowait()
                if newer is None or isinstance(newer, BaseException):
                    queue.put_nowait(newer)
                    break
                item = newer
                skipped += 1
            if skipped:
                logger.warning("stale_observations_skipped", count=skipped)
            if item is None:
                return
            if isinstance(item, BaseException):
                if self._stop.is_set():
                    return
                raise item
            yield item

    # -- actions ------------------------------------------------------------

    async def submit_intent(self, tick_id: int, intent: pb.Intent) -> IntentResult:
        """Submit this tick's intent and report whether the world took it."""
        response = await asyncio.to_thread(
            self._action_stub.SubmitIntent,
            pb.SubmitIntentRequest(
                lease_id=self.lease_id,
                entity_id=self.entity_id,
                tick_id=tick_id,
                intent=intent,
            ),
        )
        return IntentResult(accepted=response.accepted, reason=response.reason)

    async def report_status(
        self,
        mode: str,
        brief: str,
        planner_thought: str,
        stint_json: str,
        cost_json: str = "",
    ) -> bool:
        """Push the agent's internal state to the world for the viewer."""
        response = await asyncio.to_thread(
            self._status_stub.ReportStatus,
            pb.AgentStatusReport(
                lease_id=self.lease_id,
                entity_id=self.entity_id,
                mode=mode,
                brief=brief,
                planner_thought=planner_thought,
                stint_json=stint_json,
                cost_json=cost_json,
            ),
        )
        return bool(response.accepted)
