"""ActionService gRPC implementation."""

import grpc
import structlog

from .. import world_pb2 as pb
from .. import world_pb2_grpc
from ..conversion import direction_from_proto
from ..lease import LeaseManager
from ..tick import TickLoop
from ..types import (
    AttackIntent,
    CollectIntent,
    CraftIntent,
    DepositIntent,
    DropIntent,
    EatIntent,
    EntityIntent,
    EquipIntent,
    ExtractIntent,
    MoveIntent,
    PickupIntent,
    PlaceIntent,
    SayIntent,
    WaitIntent,
    WithdrawIntent,
    WriteNoteIntent,
)

logger = structlog.get_logger()


class IntentConversionError(Exception):
    """Raised when a proto intent cannot become an internal intent model."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def intent_from_proto(entity_id: str, intent: pb.Intent) -> EntityIntent:
    """Convert a proto Intent into the matching internal intent model.

    Raises:
        IntentConversionError: If the action is unknown or malformed.
    """
    action = intent.WhichOneof("action")

    if action == "move":
        direction = direction_from_proto(intent.move.direction)
        if direction is None:
            raise IntentConversionError("invalid_direction")
        return MoveIntent(entity_id=entity_id, direction=direction)

    if action == "collect":
        return CollectIntent(
            entity_id=entity_id,
            object_id=intent.collect.object_id or None,
            item_type=intent.collect.item_type or "berry",
        )

    if action == "eat":
        if not intent.eat.item_type:
            raise IntentConversionError("missing_item_type")
        return EatIntent(
            entity_id=entity_id,
            item_type=intent.eat.item_type,
            amount=intent.eat.amount or 1,
        )

    if action == "attack":
        if not intent.attack.target_entity_id:
            raise IntentConversionError("missing_target")
        return AttackIntent(
            entity_id=entity_id,
            target_entity_id=intent.attack.target_entity_id,
        )

    if action == "extract":
        if not intent.extract.object_id:
            raise IntentConversionError("missing_object_id")
        return ExtractIntent(entity_id=entity_id, object_id=intent.extract.object_id)

    if action == "pickup":
        if not intent.pickup.kind:
            raise IntentConversionError("missing_kind")
        return PickupIntent(
            entity_id=entity_id,
            kind=intent.pickup.kind,
            amount=intent.pickup.amount or 1,
        )

    if action == "withdraw":
        if not intent.withdraw.object_id or not intent.withdraw.kind:
            raise IntentConversionError("missing_object_id_or_kind")
        return WithdrawIntent(
            entity_id=entity_id,
            object_id=intent.withdraw.object_id,
            kind=intent.withdraw.kind,
            amount=intent.withdraw.amount or 1,
        )

    if action == "drop":
        if not intent.drop.kind:
            raise IntentConversionError("missing_kind")
        return DropIntent(
            entity_id=entity_id,
            kind=intent.drop.kind,
            amount=intent.drop.amount or 1,
        )

    if action == "deposit":
        if not intent.deposit.object_id or not intent.deposit.kind:
            raise IntentConversionError("missing_object_id_or_kind")
        return DepositIntent(
            entity_id=entity_id,
            object_id=intent.deposit.object_id,
            kind=intent.deposit.kind,
            amount=intent.deposit.amount or 1,
        )

    if action == "craft":
        if not intent.craft.recipe:
            raise IntentConversionError("missing_recipe")
        return CraftIntent(entity_id=entity_id, recipe=intent.craft.recipe)

    if action == "equip":
        return EquipIntent(entity_id=entity_id, kind=intent.equip.kind)

    if action == "place":
        direction = direction_from_proto(intent.place.direction)
        if direction is None:
            raise IntentConversionError("invalid_direction")
        if not intent.place.kind:
            raise IntentConversionError("missing_kind")
        return PlaceIntent(
            entity_id=entity_id, kind=intent.place.kind, direction=direction
        )

    if action == "write_note":
        if not intent.write_note.object_id:
            raise IntentConversionError("missing_object_id")
        return WriteNoteIntent(
            entity_id=entity_id,
            object_id=intent.write_note.object_id,
            slot=intent.write_note.slot,
            title=intent.write_note.title,
            text=intent.write_note.text,
        )

    if action == "say":
        return SayIntent(
            entity_id=entity_id,
            text=intent.say.text,
            channel=intent.say.channel or "local",
        )

    if action == "wait":
        return WaitIntent(entity_id=entity_id)

    raise IntentConversionError("unknown_action")


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
