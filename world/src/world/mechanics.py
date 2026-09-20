"""The registry of everything an agent can ask the world to do.

One row per intent, holding the four things that used to be spread over four
modules: the internal intent model, the `action_type` string that names it in
results and logs, the proto `Intent` oneof field it arrives in with the
function that converts it, and the phase that applies it.

`MECHANICS` is declared **in phase order**: that order is a behavioural
contract (docs/05_jev_agents_design.md) and `tick.process_tick` walks this
table, so the order lives in exactly one place. Adding an intent means adding
one row here and one field to `proto/world.proto`.
"""

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from . import world_pb2 as pb
from .combat import process_attack_phase
from .containers import (
    process_deposit_phase,
    process_drop_phase,
    process_equip_phase,
    process_give_phase,
    process_pickup_phase,
    process_place_phase,
    process_withdraw_phase,
    process_write_note_phase,
)
from .conversations import process_conversation_phase
from .conversion import direction_from_proto
from .crafting import process_craft_phase
from .events import TickEvents
from .foraging import process_collect_phase, process_eat_phase, process_extract_phase
from .sleep import process_sleep_phase, process_wake_phase
from .speech import process_say_phase
from .state import World
from .stats import process_rest_phase
from .types import (
    CONVERSE_ACTIONS,
    AttackIntent,
    CollectIntent,
    ConverseIntent,
    CraftIntent,
    DepositIntent,
    DropIntent,
    EatIntent,
    EntityIntent,
    EquipIntent,
    ExtractIntent,
    GiveIntent,
    MoveIntent,
    PickupIntent,
    PlaceIntent,
    RestIntent,
    SayIntent,
    SleepIntent,
    WaitIntent,
    WakeIntent,
    WithdrawIntent,
    WriteNoteIntent,
)

__all__ = [
    "INTENT_ACTION_TYPES",
    "MECHANICS",
    "IntentConversionError",
    "Mechanic",
    "intent_from_proto",
    "sleeper_may_submit",
]


class IntentConversionError(Exception):
    """Raised when a proto intent cannot become an internal intent model."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# A phase applies every intent of one kind and records what happened. The
# second argument is really `Mapping[str, <that row's intent model>]`; it is
# spelled `Any` because `Mapping` is invariant in its value type, so no single
# alias covers every concrete phase signature.
IntentPhase = Callable[[World, Any, TickEvents], None]

# A converter builds one internal intent from the proto it arrived in.
FromProto = Callable[[str, pb.Intent], EntityIntent]


@dataclass(frozen=True)
class Mechanic:
    """One intent, end to end."""

    intent: type[EntityIntent]
    action_type: str
    # The `Intent` oneof field this arrives in (`proto/world.proto`).
    proto_field: str
    from_proto: FromProto
    # The phase that applies it, or None for the one mechanic `process_tick`
    # runs itself: movement, which resolves conflicts between entities rather
    # than applying intents one by one.
    phase: IntentPhase | None
    # True only for `wake`, the one thing a sleeping settler may ask for
    # (docs/10_metal_and_sleep.md). Every other intent from a sleeper is
    # refused on submission, and the wake phase does its own filtering.
    allowed_while_asleep: bool = False


# --- Converters -----------------------------------------------------------
#
# Each one validates what the proto cannot: a required field is a non-empty
# string there, so "missing" has to be checked here, at the boundary.


def _move_from_proto(entity_id: str, intent: pb.Intent) -> EntityIntent:
    direction = direction_from_proto(intent.move.direction)
    if direction is None:
        raise IntentConversionError("invalid_direction")
    return MoveIntent(entity_id=entity_id, direction=direction)


def _attack_from_proto(entity_id: str, intent: pb.Intent) -> EntityIntent:
    if not intent.attack.target_entity_id:
        raise IntentConversionError("missing_target")
    return AttackIntent(
        entity_id=entity_id, target_entity_id=intent.attack.target_entity_id
    )


def _give_from_proto(entity_id: str, intent: pb.Intent) -> EntityIntent:
    if not intent.give.target_entity_id:
        raise IntentConversionError("missing_target")
    if not intent.give.kind:
        raise IntentConversionError("missing_kind")
    return GiveIntent(
        entity_id=entity_id,
        target_entity_id=intent.give.target_entity_id,
        kind=intent.give.kind,
        amount=intent.give.amount or 1,
    )


def _converse_from_proto(entity_id: str, intent: pb.Intent) -> EntityIntent:
    if intent.converse.action not in CONVERSE_ACTIONS:
        raise IntentConversionError("unknown_converse_action")
    return ConverseIntent(
        entity_id=entity_id,
        action=intent.converse.action,
        direction=direction_from_proto(intent.converse.direction),
        conversation_id=intent.converse.conversation_id,
        text=intent.converse.text,
        target_entity_id=intent.converse.target_entity_id,
    )


def _extract_from_proto(entity_id: str, intent: pb.Intent) -> EntityIntent:
    if not intent.extract.object_id:
        raise IntentConversionError("missing_object_id")
    return ExtractIntent(entity_id=entity_id, object_id=intent.extract.object_id)


def _collect_from_proto(entity_id: str, intent: pb.Intent) -> EntityIntent:
    return CollectIntent(
        entity_id=entity_id,
        object_id=intent.collect.object_id or None,
        item_type=intent.collect.item_type or "berry",
    )


def _pickup_from_proto(entity_id: str, intent: pb.Intent) -> EntityIntent:
    if not intent.pickup.kind:
        raise IntentConversionError("missing_kind")
    return PickupIntent(
        entity_id=entity_id,
        kind=intent.pickup.kind,
        amount=intent.pickup.amount or 1,
    )


def _withdraw_from_proto(entity_id: str, intent: pb.Intent) -> EntityIntent:
    if not intent.withdraw.object_id or not intent.withdraw.kind:
        raise IntentConversionError("missing_object_id_or_kind")
    return WithdrawIntent(
        entity_id=entity_id,
        object_id=intent.withdraw.object_id,
        kind=intent.withdraw.kind,
        amount=intent.withdraw.amount or 1,
    )


def _drop_from_proto(entity_id: str, intent: pb.Intent) -> EntityIntent:
    if not intent.drop.kind:
        raise IntentConversionError("missing_kind")
    return DropIntent(
        entity_id=entity_id, kind=intent.drop.kind, amount=intent.drop.amount or 1
    )


def _deposit_from_proto(entity_id: str, intent: pb.Intent) -> EntityIntent:
    if not intent.deposit.object_id or not intent.deposit.kind:
        raise IntentConversionError("missing_object_id_or_kind")
    return DepositIntent(
        entity_id=entity_id,
        object_id=intent.deposit.object_id,
        kind=intent.deposit.kind,
        amount=intent.deposit.amount or 1,
    )


def _craft_from_proto(entity_id: str, intent: pb.Intent) -> EntityIntent:
    if not intent.craft.recipe:
        raise IntentConversionError("missing_recipe")
    return CraftIntent(entity_id=entity_id, recipe=intent.craft.recipe)


def _equip_from_proto(entity_id: str, intent: pb.Intent) -> EntityIntent:
    return EquipIntent(entity_id=entity_id, kind=intent.equip.kind)


def _place_from_proto(entity_id: str, intent: pb.Intent) -> EntityIntent:
    if not intent.place.kind:
        raise IntentConversionError("missing_kind")
    # An unspecified direction means "my own tile"; only ground-layer kinds
    # may use it, which the place phase checks.
    return PlaceIntent(
        entity_id=entity_id,
        kind=intent.place.kind,
        direction=direction_from_proto(intent.place.direction),
    )


def _write_note_from_proto(entity_id: str, intent: pb.Intent) -> EntityIntent:
    if not intent.write_note.object_id:
        raise IntentConversionError("missing_object_id")
    return WriteNoteIntent(
        entity_id=entity_id,
        object_id=intent.write_note.object_id,
        slot=intent.write_note.slot,
        title=intent.write_note.title,
        text=intent.write_note.text,
    )


def _eat_from_proto(entity_id: str, intent: pb.Intent) -> EntityIntent:
    if not intent.eat.item_type:
        raise IntentConversionError("missing_item_type")
    return EatIntent(
        entity_id=entity_id,
        item_type=intent.eat.item_type,
        amount=intent.eat.amount or 1,
    )


def _rest_from_proto(entity_id: str, intent: pb.Intent) -> EntityIntent:
    if not intent.rest.object_id:
        raise IntentConversionError("missing_object_id")
    return RestIntent(entity_id=entity_id, object_id=intent.rest.object_id)


def _say_from_proto(entity_id: str, intent: pb.Intent) -> EntityIntent:
    return SayIntent(
        entity_id=entity_id,
        text=intent.say.text,
        channel=intent.say.channel or "local",
    )


def _wait_from_proto(entity_id: str, intent: pb.Intent) -> EntityIntent:
    return WaitIntent(entity_id=entity_id)


def _wake_from_proto(entity_id: str, intent: pb.Intent) -> EntityIntent:
    return WakeIntent(entity_id=entity_id)


def _sleep_from_proto(entity_id: str, intent: pb.Intent) -> EntityIntent:
    return SleepIntent(entity_id=entity_id, object_id=intent.sleep.object_id)


# --- Phases without a module of their own ---------------------------------


def process_wait_phase(
    world: World, intents: Mapping[str, WaitIntent], events: TickEvents
) -> None:
    """Waiting always succeeds and changes nothing."""
    for entity_id in sorted(intents):
        events.acted(entity_id, "wait", True, "")


# --- The table ------------------------------------------------------------
#
# In phase order. The reasons the order is what it is:
#   - movement first, so every other phase sees this tick's positions;
#   - attack before give and conversations, so a fatal blow lands first;
#   - give just before conversations, so a hand-over still works on the tick
#     a conversation closes;
#   - eat, rest and say after the inventory and building phases;
#   - wake before sleep, so someone who woke this tick cannot lie down again
#     within it;
#   - sleep last of the intent phases, after movement and every action.

MECHANICS: tuple[Mechanic, ...] = (
    Mechanic(MoveIntent, "move", "move", _move_from_proto, phase=None),
    Mechanic(
        AttackIntent, "attack", "attack", _attack_from_proto, process_attack_phase
    ),
    Mechanic(GiveIntent, "give", "give", _give_from_proto, process_give_phase),
    Mechanic(
        ConverseIntent,
        "converse",
        "converse",
        _converse_from_proto,
        process_conversation_phase,
    ),
    Mechanic(
        ExtractIntent, "extract", "extract", _extract_from_proto, process_extract_phase
    ),
    Mechanic(
        CollectIntent, "collect", "collect", _collect_from_proto, process_collect_phase
    ),
    Mechanic(
        PickupIntent, "pickup", "pickup", _pickup_from_proto, process_pickup_phase
    ),
    Mechanic(
        WithdrawIntent,
        "withdraw",
        "withdraw",
        _withdraw_from_proto,
        process_withdraw_phase,
    ),
    Mechanic(DropIntent, "drop", "drop", _drop_from_proto, process_drop_phase),
    Mechanic(
        DepositIntent, "deposit", "deposit", _deposit_from_proto, process_deposit_phase
    ),
    Mechanic(CraftIntent, "craft", "craft", _craft_from_proto, process_craft_phase),
    Mechanic(EquipIntent, "equip", "equip", _equip_from_proto, process_equip_phase),
    Mechanic(PlaceIntent, "place", "place", _place_from_proto, process_place_phase),
    Mechanic(
        WriteNoteIntent,
        "write_note",
        "write_note",
        _write_note_from_proto,
        process_write_note_phase,
    ),
    Mechanic(EatIntent, "eat", "eat", _eat_from_proto, process_eat_phase),
    Mechanic(RestIntent, "rest", "rest", _rest_from_proto, process_rest_phase),
    Mechanic(SayIntent, "say", "say", _say_from_proto, process_say_phase),
    Mechanic(WaitIntent, "wait", "wait", _wait_from_proto, process_wait_phase),
    Mechanic(
        WakeIntent,
        "wake",
        "wake",
        _wake_from_proto,
        process_wake_phase,
        allowed_while_asleep=True,
    ),
    Mechanic(SleepIntent, "sleep", "sleep", _sleep_from_proto, process_sleep_phase),
)

# Intent model -> the action_type string used in results and logs.
INTENT_ACTION_TYPES: Mapping[type[EntityIntent], str] = {
    mechanic.intent: mechanic.action_type for mechanic in MECHANICS
}

_BY_PROTO_FIELD: Mapping[str, Mechanic] = {
    mechanic.proto_field: mechanic for mechanic in MECHANICS
}

_ASLEEP_ALLOWED: frozenset[type[EntityIntent]] = frozenset(
    mechanic.intent for mechanic in MECHANICS if mechanic.allowed_while_asleep
)


def sleeper_may_submit(intent: EntityIntent) -> bool:
    """Whether a sleeping settler is allowed to submit this intent."""
    return type(intent) in _ASLEEP_ALLOWED


def intent_from_proto(entity_id: str, intent: pb.Intent) -> EntityIntent:
    """Convert a proto Intent into the matching internal intent model.

    Raises:
        IntentConversionError: If the action is unknown or malformed.
    """
    action = intent.WhichOneof("action")
    mechanic = _BY_PROTO_FIELD.get(action or "")
    if mechanic is None:
        raise IntentConversionError("unknown_action")
    return mechanic.from_proto(entity_id, intent)
