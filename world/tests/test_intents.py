"""Tests for intent submission, the dispatcher and the full tick pipeline."""

import time

import pytest

from world import world_pb2 as pb
from world.events import TickEvents
from world.services.action_service import IntentConversionError, intent_from_proto
from world.state import Entity, Inventory, World, WorldObject
from world.tick import process_tick, run_ticks
from world.tick_context import TickContext
from world.types import (
    AttackIntent,
    CollectIntent,
    ConverseIntent,
    CraftIntent,
    DepositIntent,
    Direction,
    DropIntent,
    EatIntent,
    EquipIntent,
    ExtractIntent,
    GiveIntent,
    MoveIntent,
    PickupIntent,
    PlaceIntent,
    RestIntent,
    Position,
    SayIntent,
    WaitIntent,
    WithdrawIntent,
    WriteNoteIntent,
)


def _world() -> World:
    world = World(width=20, height=20)
    world.add_entity(
        Entity(
            entity_id="bob",
            position=Position(x=5, y=5),
            inventory=Inventory().add("berry", 2).add("wood", 6),
        )
    )
    return world


def _context(world: World, deadline_offset_ms: int = 10_000) -> TickContext:
    now = int(time.time() * 1000)
    return TickContext(
        tick_id=world.tick,
        start_time_ms=now,
        deadline_ms=now + deadline_offset_ms,
        world=world,
    )


class TestSubmitIntent:
    def test_accepts_one_intent_per_entity(self) -> None:
        world = _world()
        ctx = _context(world)

        accepted, reason = ctx.submit_intent(
            "bob", MoveIntent(entity_id="bob", direction=Direction.NORTH)
        )
        assert (accepted, reason) == (True, "")

        accepted, reason = ctx.submit_intent("bob", WaitIntent(entity_id="bob"))
        assert (accepted, reason) == (False, "duplicate")
        assert isinstance(ctx.intents["bob"], MoveIntent)

    def test_rejects_after_deadline(self) -> None:
        world = _world()
        ctx = _context(world, deadline_offset_ms=-1000)

        accepted, reason = ctx.submit_intent("bob", WaitIntent(entity_id="bob"))

        assert (accepted, reason) == (False, "late_tick")

    def test_wolf_submission_may_ignore_the_deadline(self) -> None:
        world = _world()
        ctx = _context(world, deadline_offset_ms=-1000)

        accepted, reason = ctx.submit_intent(
            "bob", WaitIntent(entity_id="bob"), enforce_deadline=False
        )

        assert (accepted, reason) == (True, "")

    def test_rejects_dead_entities(self) -> None:
        world = _world()
        bob = world.get_entity("bob")
        world.detach_entity("bob")
        world.set_entity(bob.as_dead())
        ctx = _context(world)

        accepted, reason = ctx.submit_intent("bob", WaitIntent(entity_id="bob"))

        assert (accepted, reason) == (False, "dead")

    def test_mismatched_entity_id_raises(self) -> None:
        world = _world()
        ctx = _context(world)

        with pytest.raises(ValueError):
            ctx.submit_intent("alice", WaitIntent(entity_id="bob"))

    def test_typed_helpers_all_route_through_the_dispatcher(self) -> None:
        world = _world()
        intents = [
            MoveIntent(entity_id="bob", direction=Direction.NORTH),
            CollectIntent(entity_id="bob"),
            EatIntent(entity_id="bob", item_type="berry"),
            AttackIntent(entity_id="bob", target_entity_id="alice"),
            ExtractIntent(entity_id="bob", object_id="tree_1"),
            PickupIntent(entity_id="bob", kind="wood"),
            WithdrawIntent(entity_id="bob", object_id="chest_1", kind="wood"),
            DropIntent(entity_id="bob", kind="wood"),
            DepositIntent(entity_id="bob", object_id="chest_1", kind="wood"),
            CraftIntent(entity_id="bob", recipe="axe"),
            EquipIntent(entity_id="bob", kind="axe"),
            PlaceIntent(entity_id="bob", kind="chest", direction=Direction.EAST),
            PlaceIntent(entity_id="bob", kind="road"),
            RestIntent(entity_id="bob", object_id="bed_1"),
            WriteNoteIntent(entity_id="bob", object_id="board_1", slot=0),
            SayIntent(entity_id="bob", text="hi"),
            ConverseIntent(
                entity_id="bob", action="open", direction=Direction.EAST, text="hi"
            ),
            GiveIntent(entity_id="bob", target_entity_id="alice", kind="wood"),
            WaitIntent(entity_id="bob"),
        ]

        for intent in intents:
            ctx = _context(world)
            assert ctx.submit_intent("bob", intent) == (True, "")
            assert ctx.intents["bob"] is intent

    def test_move_intents_view(self) -> None:
        world = _world()
        ctx = _context(world)
        ctx.submit_move_intent("bob", Direction.SOUTH)

        assert ctx.move_intents == {"bob": Direction.SOUTH}
        assert ctx.collect_intents == {}


class TestProtoConversion:
    def test_every_action_converts(self) -> None:
        cases = [
            (pb.Intent(move=pb.MoveIntent(direction=pb.NORTH)), MoveIntent),
            (pb.Intent(collect=pb.CollectIntent()), CollectIntent),
            (pb.Intent(eat=pb.EatIntent(item_type="berry")), EatIntent),
            (pb.Intent(attack=pb.AttackIntent(target_entity_id="x")), AttackIntent),
            (pb.Intent(extract=pb.ExtractIntent(object_id="t")), ExtractIntent),
            (pb.Intent(pickup=pb.PickupIntent(kind="wood")), PickupIntent),
            (
                pb.Intent(withdraw=pb.WithdrawIntent(object_id="c", kind="wood")),
                WithdrawIntent,
            ),
            (pb.Intent(drop=pb.DropIntent(kind="wood")), DropIntent),
            (
                pb.Intent(deposit=pb.DepositIntent(object_id="c", kind="wood")),
                DepositIntent,
            ),
            (pb.Intent(craft=pb.CraftIntent(recipe="axe")), CraftIntent),
            (pb.Intent(equip=pb.EquipIntent(kind="axe")), EquipIntent),
            (
                pb.Intent(place=pb.PlaceIntent(kind="chest", direction=pb.EAST)),
                PlaceIntent,
            ),
            (
                pb.Intent(write_note=pb.WriteNoteIntent(object_id="b", slot=1)),
                WriteNoteIntent,
            ),
            (pb.Intent(rest=pb.RestIntent(object_id="bed_1")), RestIntent),
            (pb.Intent(say=pb.SayIntent(text="hi")), SayIntent),
            (
                pb.Intent(
                    converse=pb.ConverseIntent(
                        action="open", direction=pb.EAST, text="hello"
                    )
                ),
                ConverseIntent,
            ),
            (
                pb.Intent(give=pb.GiveIntent(target_entity_id="mira", kind="stone")),
                GiveIntent,
            ),
            (pb.Intent(wait=pb.WaitIntent()), WaitIntent),
        ]

        for proto_intent, expected in cases:
            converted = intent_from_proto("bob", proto_intent)
            assert isinstance(converted, expected)
            assert converted.entity_id == "bob"

    def test_defaults_are_filled_in(self) -> None:
        pickup = intent_from_proto(
            "bob", pb.Intent(pickup=pb.PickupIntent(kind="wood"))
        )
        assert isinstance(pickup, PickupIntent)
        assert pickup.amount == 1

        say = intent_from_proto("bob", pb.Intent(say=pb.SayIntent(text="hi")))
        assert isinstance(say, SayIntent)
        assert say.channel == "local"

        give = intent_from_proto(
            "bob", pb.Intent(give=pb.GiveIntent(target_entity_id="mira", kind="stone"))
        )
        assert isinstance(give, GiveIntent)
        assert give.amount == 1

        # An unspecified direction is only meaningful for `open`.
        speak = intent_from_proto(
            "bob", pb.Intent(converse=pb.ConverseIntent(action="speak", text="hi"))
        )
        assert isinstance(speak, ConverseIntent)
        assert speak.direction is None

    def test_malformed_intents_raise(self) -> None:
        for proto_intent in (
            pb.Intent(),
            pb.Intent(move=pb.MoveIntent()),
            pb.Intent(eat=pb.EatIntent()),
            pb.Intent(attack=pb.AttackIntent()),
            pb.Intent(craft=pb.CraftIntent()),
            pb.Intent(rest=pb.RestIntent()),
            pb.Intent(place=pb.PlaceIntent(direction=pb.EAST)),
            pb.Intent(converse=pb.ConverseIntent()),
            pb.Intent(converse=pb.ConverseIntent(action="shout")),
            pb.Intent(give=pb.GiveIntent(kind="stone")),
            pb.Intent(give=pb.GiveIntent(target_entity_id="mira")),
        ):
            with pytest.raises(IntentConversionError):
                intent_from_proto("bob", proto_intent)

    def test_place_without_a_direction_means_the_entitys_own_tile(self) -> None:
        place = intent_from_proto("bob", pb.Intent(place=pb.PlaceIntent(kind="road")))

        assert isinstance(place, PlaceIntent)
        assert place.direction is None


class TestTickPipeline:
    def test_wait_and_say_produce_action_results(self) -> None:
        world = _world()
        world.add_entity(Entity(entity_id="alice", position=Position(x=1, y=1)))
        ctx = _context(world)
        ctx.submit_intent("bob", WaitIntent(entity_id="bob"))
        ctx.submit_intent("alice", SayIntent(entity_id="alice", text="hello"))

        result = process_tick(world, ctx)

        kinds = {(r.entity_id, r.action_type, r.success) for r in result.action_results}
        assert ("bob", "wait", True) in kinds
        assert ("alice", "say", True) in kinds
        assert result.utterances[0].text == "hello"
        assert result.utterances[0].position == Position(x=1, y=1)

    def test_thought_channel_is_kept_as_an_utterance(self) -> None:
        world = _world()
        ctx = _context(world)
        ctx.submit_intent(
            "bob", SayIntent(entity_id="bob", text="hmm", channel="thought")
        )

        result = process_tick(world, ctx)

        assert result.utterances[0].channel == "thought"

    def test_shout_channel_is_accepted(self) -> None:
        world = _world()
        ctx = _context(world)
        ctx.submit_intent(
            "bob", SayIntent(entity_id="bob", text="wolf!", channel="shout")
        )

        result = process_tick(world, ctx)

        assert result.utterances[0].channel == "shout"

    def test_attack_then_death_populates_every_list(self) -> None:
        world = _world()
        world.add_entity(Entity(entity_id="alice", position=Position(x=6, y=5)))
        alice = world.get_entity("alice")
        world.set_entity(
            alice.with_health(1).with_inventory(Inventory().add("stone", 1))
        )
        ctx = _context(world)
        ctx.submit_intent(
            "bob", AttackIntent(entity_id="bob", target_entity_id="alice")
        )

        result = process_tick(world, ctx)

        assert result.damage_events[0].entity_id == "alice"
        assert result.deaths[0].entity_id == "alice"
        assert result.objects_added[0].obj.object_type == "item_pile"
        assert any(
            r.action_type == "attack" and r.success for r in result.action_results
        )

    def test_dead_entities_cannot_act_later_in_the_tick(self) -> None:
        world = _world()
        world.add_entity(Entity(entity_id="alice", position=Position(x=6, y=5)))
        world.set_entity(world.get_entity("alice").with_health(1))
        ctx = _context(world)
        ctx.submit_intent(
            "bob", AttackIntent(entity_id="bob", target_entity_id="alice")
        )
        # alice submitted a craft before dying this tick
        ctx.submit_intent("alice", CraftIntent(entity_id="alice", recipe="axe"))

        result = process_tick(world, ctx)

        craft = next(r for r in result.action_results if r.action_type == "craft")
        assert not craft.success
        assert craft.details == "dead"

    def test_collect_and_eat_appear_in_action_results(self) -> None:
        world = _world()
        world.add_object(
            WorldObject(
                object_id="bush_1",
                position=Position(x=5, y=5),
                object_type="bush",
                state=(("berry_count", "1"),),
            )
        )
        ctx = _context(world)
        ctx.submit_intent("bob", CollectIntent(entity_id="bob"))

        result = process_tick(world, ctx)

        assert any(
            r.action_type == "collect" and r.success for r in result.action_results
        )
        assert result.collect_results[0].success

    @pytest.mark.asyncio
    async def test_run_ticks_still_drives_movement(self) -> None:
        world = _world()

        async def submit(ctx: TickContext) -> None:
            ctx.submit_move_intent("bob", Direction.EAST)

        results = await run_ticks(world, num_ticks=2, intent_callback=submit)

        assert world.get_entity("bob").position == Position(x=7, y=5)
        assert all(r.move_results[0].success for r in results)

    def test_events_accumulator_records_actions(self) -> None:
        events = TickEvents()
        events.acted("bob", "wait", True, "")
        assert events.action_results[0].action_type == "wait"
