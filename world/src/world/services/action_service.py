"""ActionService gRPC implementation."""

import grpc
import structlog

from .. import world_pb2 as pb
from .. import world_pb2_grpc
from ..lease import LeaseManager
from ..mechanics import IntentConversionError, intent_from_proto
from ..tick import TickLoop

__all__ = [
    "ActionServiceServicer",
    # Re-exported: the conversion itself lives in `mechanics.py`, beside the
    # rest of each intent's definition.
    "IntentConversionError",
    "intent_from_proto",
]

logger = structlog.get_logger()


class ActionServiceServicer(world_pb2_grpc.ActionServiceServicer):
    """Implements the ActionService for submitting intents."""

    def __init__(self, tick_loop: TickLoop, lease_manager: LeaseManager):
        self.tick_loop = tick_loop
        self.lease_manager = lease_manager

    def SubmitIntent(
        self, request: pb.SubmitIntentRequest, context: grpc.ServicerContext
    ) -> pb.SubmitIntentResponse:
        """Submit an intent for the current tick."""
        lease_id = request.lease_id
        entity_id = request.entity_id
        tick_id = request.tick_id

        if not self.lease_manager.is_valid_lease(lease_id, entity_id):
            logger.debug(
                "intent_rejected_invalid_lease",
                lease_id=lease_id,
                entity_id=entity_id,
            )
            return pb.SubmitIntentResponse(accepted=False, reason="invalid_lease")

        current_ctx = self.tick_loop.current_context
        if current_ctx is None:
            return pb.SubmitIntentResponse(accepted=False, reason="no_tick_in_progress")

        if tick_id != current_ctx.tick_id:
            logger.debug(
                "intent_rejected_wrong_tick",
                submitted_tick=tick_id,
                current_tick=current_ctx.tick_id,
            )
            return pb.SubmitIntentResponse(accepted=False, reason="wrong_tick")

        try:
            intent = intent_from_proto(entity_id, request.intent)
        except IntentConversionError as exc:
            logger.debug(
                "intent_rejected_conversion", entity_id=entity_id, reason=exc.reason
            )
            return pb.SubmitIntentResponse(accepted=False, reason=exc.reason)

        accepted, reason = current_ctx.submit_intent(entity_id, intent)
        if not accepted:
            return pb.SubmitIntentResponse(accepted=False, reason=reason)

        logger.debug(
            "intent_accepted",
            entity_id=entity_id,
            kind=type(intent).__name__,
            tick_id=tick_id,
        )
        return pb.SubmitIntentResponse(accepted=True)
