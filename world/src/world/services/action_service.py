"""ActionService gRPC implementation."""

import grpc
import structlog

from .. import world_pb2 as pb
from .. import world_pb2_grpc
from ..conversion import direction_from_proto
from ..lease import LeaseManager
from ..tick import TickLoop
from ..types import CollectIntent, EatIntent

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
        intent = request.intent

        # Validate lease
        if not self.lease_manager.is_valid_lease(lease_id, entity_id):
            logger.debug(
                "intent_rejected_invalid_lease",
                lease_id=lease_id,
                entity_id=entity_id,
            )
            return pb.SubmitIntentResponse(accepted=False, reason="invalid_lease")

        # Check tick
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

        # Handle the specific intent type
        action_type = intent.WhichOneof("action")

        if action_type == "move":
            return self._handle_move_intent(entity_id, intent.move)
        elif action_type == "collect":
            return self._handle_collect_intent(entity_id, intent.collect)
        elif action_type == "eat":
            return self._handle_eat_intent(entity_id, intent.eat)
        elif action_type == "wait":
            # Wait is a no-op, always accepted
            return pb.SubmitIntentResponse(accepted=True)
        elif action_type in ("pickup", "use", "say"):
            # Not implemented yet
            return pb.SubmitIntentResponse(
                accepted=False, reason=f"{action_type}_not_implemented"
            )
        else:
            return pb.SubmitIntentResponse(accepted=False, reason="unknown_action")

    def _handle_move_intent(
        self, entity_id: str, move_intent: pb.MoveIntent
    ) -> pb.SubmitIntentResponse:
        """Handle a move intent submission."""
        direction = direction_from_proto(move_intent.direction)

        if direction is None:
            return pb.SubmitIntentResponse(
                accepted=False, reason="invalid_direction"
            )

        accepted = self.tick_loop.submit_move_intent(entity_id, direction)

        if not accepted:
            return pb.SubmitIntentResponse(accepted=False, reason="late_or_duplicate")

        logger.debug(
            "move_intent_accepted",
            entity_id=entity_id,
            direction=direction.name,
        )

        return pb.SubmitIntentResponse(accepted=True)

    def _handle_collect_intent(
        self, entity_id: str, collect_intent: pb.CollectIntent
    ) -> pb.SubmitIntentResponse:
        """Handle a collect intent submission."""
        intent = CollectIntent(
            entity_id=entity_id,
            object_id=collect_intent.object_id or None,
            item_type=collect_intent.item_type or "berry",
            amount=collect_intent.amount or 1,
        )

        ctx = self.tick_loop.current_context
        if ctx is None:
            return pb.SubmitIntentResponse(accepted=False, reason="no_tick_in_progress")

        accepted = ctx.submit_collect_intent(intent)

        if not accepted:
            return pb.SubmitIntentResponse(accepted=False, reason="late_or_duplicate")

        logger.debug(
            "collect_intent_accepted",
            entity_id=entity_id,
            object_id=intent.object_id,
            item_type=intent.item_type,
        )

        return pb.SubmitIntentResponse(accepted=True)

    def _handle_eat_intent(
        self, entity_id: str, eat_intent: pb.EatIntent
    ) -> pb.SubmitIntentResponse:
        """Handle an eat intent submission."""
        if not eat_intent.item_type:
            return pb.SubmitIntentResponse(accepted=False, reason="missing_item_type")

        intent = EatIntent(
            entity_id=entity_id,
            item_type=eat_intent.item_type,
            amount=eat_intent.amount or 1,
        )

        ctx = self.tick_loop.current_context
        if ctx is None:
            return pb.SubmitIntentResponse(accepted=False, reason="no_tick_in_progress")

        accepted = ctx.submit_eat_intent(intent)

        if not accepted:
            return pb.SubmitIntentResponse(accepted=False, reason="late_or_duplicate")

        logger.debug(
            "eat_intent_accepted",
            entity_id=entity_id,
            item_type=intent.item_type,
            amount=intent.amount,
        )

        return pb.SubmitIntentResponse(accepted=True)
