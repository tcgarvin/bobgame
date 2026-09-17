"""AgentStatusService gRPC implementation.

Agents report their internal state (mode, brief, planner thought, last Jev
decision) so the viewer can show what each actor is thinking. The report is
purely informational: it never touches world state.
"""

import json
from typing import Any, Callable

import grpc
import structlog

from .. import world_pb2 as pb
from .. import world_pb2_grpc
from ..lease import LeaseManager

logger = structlog.get_logger()

# Callback that pushes a JSON-serialisable message to viewer clients.
BroadcastCallback = Callable[[dict[str, Any]], None]


def _ignore(message: dict[str, Any]) -> None:
    """Default recorder callback: drop the message."""


class AgentStatusServiceServicer(world_pb2_grpc.AgentStatusServiceServicer):
    """Accepts agent status reports, forwards them to the viewer and recorder."""

    def __init__(
        self,
        lease_manager: LeaseManager,
        broadcast: BroadcastCallback,
        record: BroadcastCallback = _ignore,
    ):
        self.lease_manager = lease_manager
        self.broadcast = broadcast
        self.record = record

    def ReportStatus(
        self, request: pb.AgentStatusReport, context: grpc.ServicerContext
    ) -> pb.AgentStatusAck:
        """Validate the lease, parse the stint payload, broadcast to viewers."""
        if not self.lease_manager.is_valid_lease(request.lease_id, request.entity_id):
            logger.debug(
                "agent_status_rejected_invalid_lease",
                entity_id=request.entity_id,
            )
            return pb.AgentStatusAck(accepted=False)

        stint: Any = None
        if request.stint_json:
            try:
                stint = json.loads(request.stint_json)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "agent_status_invalid_stint_json",
                    entity_id=request.entity_id,
                    error=str(exc),
                )
                return pb.AgentStatusAck(accepted=False)

        status = {
            "type": "agent_status",
            "entity_id": request.entity_id,
            "mode": request.mode,
            "brief": request.brief,
            "planner_thought": request.planner_thought,
            "stint": stint,
        }
        self.broadcast(status)
        self.record(status)
        logger.debug(
            "agent_status_reported",
            entity_id=request.entity_id,
            mode=request.mode,
        )
        return pb.AgentStatusAck(accepted=True)
