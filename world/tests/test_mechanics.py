"""The mechanic registry is the one place an intent is declared.

These tests fail when the registry drifts from the proto or from the rest of
the world: a new `Intent` field with no row, a duplicate action type, or an
intent whose phase never runs.
"""

from world import world_pb2 as pb
from world.mechanics import INTENT_ACTION_TYPES, MECHANICS, sleeper_may_submit
from world.types import MoveIntent, SleepIntent, WakeIntent


def _proto_intent_fields() -> set[str]:
    """Every field of the `Intent` oneof, as the proto declares it."""
    return {
        field.name for field in pb.Intent.DESCRIPTOR.oneofs_by_name["action"].fields
    }


def test_every_proto_intent_field_has_a_mechanic() -> None:
    registered = {mechanic.proto_field for mechanic in MECHANICS}
    assert registered == _proto_intent_fields()


def test_action_types_are_unique_and_complete() -> None:
    action_types = [mechanic.action_type for mechanic in MECHANICS]
    assert len(action_types) == len(set(action_types))
    assert set(INTENT_ACTION_TYPES.values()) == set(action_types)
    assert len(INTENT_ACTION_TYPES) == len(MECHANICS)


def test_movement_is_the_only_mechanic_without_a_phase() -> None:
    without_phase = [m.intent for m in MECHANICS if m.phase is None]
    assert without_phase == [MoveIntent]


def test_only_wake_is_allowed_while_asleep() -> None:
    assert sleeper_may_submit(WakeIntent(entity_id="bob"))
    assert not sleeper_may_submit(SleepIntent(entity_id="bob"))
    assert [m.intent for m in MECHANICS if m.allowed_while_asleep] == [WakeIntent]


def test_wake_runs_before_sleep() -> None:
    """A settler who woke this tick cannot lie down again within it."""
    order = [mechanic.intent for mechanic in MECHANICS]
    assert order.index(WakeIntent) < order.index(SleepIntent)
