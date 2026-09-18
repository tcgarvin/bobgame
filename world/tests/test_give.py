"""Tests for GiveIntent (docs/09_conversation_and_reflex.md, section 3)."""

import pytest

from world.items import CLAY, STONE, SWORD
from world.lease import LeaseManager
from world.services.observation_service import ObservationServiceServicer
from world.state import Entity, Inventory, World
from world.tick import TickContext, TickLoop, TickResult
from world.types import Direction, GiveIntent, MoveIntent, Position

from .test_conversations import SEATS, open_and_join, run_tick


@pytest.fixture
def world() -> World:
    """20x20 world with the conversation cast; ada carries stone and clay."""
    world = World(width=20, height=20)
    for entity_id, position in SEATS.items():
        world.add_entity(
            Entity(entity_id=entity_id, position=position, entity_type="player")
        )
    world.set_entity(
        world.get_entity("ada").with_inventory(Inventory(items=((STONE, 5), (CLAY, 2))))
    )
    return world


def give_results(result: TickResult) -> list[tuple[bool, str]]:
    """(success, details) for every give action in a tick."""
    return [
        (action.success, action.details)
        for action in result.action_results
        if action.action_type == "give"
    ]


class TestGiveAdjacent:
    def test_adjacent_transfer_moves_items_the_same_tick(self, world: World) -> None:
        # ada (4,5) and cleo (5,4) are diagonal neighbours.
        result = run_tick(
            world,
            GiveIntent(entity_id="ada", target_entity_id="cleo", kind=STONE, amount=3),
        )
        assert give_results(result) == [(True, "gave 3 stone to cleo")]
        assert world.get_entity("ada").inventory.count(STONE) == 2
        assert world.get_entity("cleo").inventory.count(STONE) == 3

    def test_amount_defaults_to_one(self, world: World) -> None:
        run_tick(world, GiveIntent(entity_id="ada", target_entity_id="cleo", kind=CLAY))
        assert world.get_entity("cleo").inventory.count(CLAY) == 1

    def test_the_receiver_sees_the_action(self, world: World) -> None:
        service = ObservationServiceServicer(world, TickLoop(world), LeaseManager())
        result = run_tick(
            world,
            GiveIntent(entity_id="ada", target_entity_id="cleo", kind=STONE),
        )
        service.on_tick_complete(result)

        observation = service._generate_observation(
            "cleo", TickContext(tick_id=1, start_time_ms=0, deadline_ms=2**60)
        )
        assert observation is not None
        acted = [
            event.entity_acted
            for event in observation.events
            if event.WhichOneof("event") == "entity_acted"
            and event.entity_acted.action_type == "give"
        ]
        assert len(acted) == 1
        assert acted[0].entity_id == "ada"
        assert acted[0].details == "gave 1 stone to cleo"

    def test_a_wielded_item_that_runs_out_is_unequipped(self, world: World) -> None:
        ada = world.get_entity("ada")
        world.set_entity(
            ada.with_inventory(ada.inventory.add(SWORD, 1)).with_wielded(SWORD)
        )
        run_tick(
            world, GiveIntent(entity_id="ada", target_entity_id="cleo", kind=SWORD)
        )
        assert world.get_entity("ada").wielded == ""
        assert world.get_entity("cleo").inventory.count(SWORD) == 1

    def test_a_wielded_item_with_some_left_stays_wielded(self, world: World) -> None:
        ada = world.get_entity("ada")
        world.set_entity(
            ada.with_inventory(ada.inventory.add(SWORD, 2)).with_wielded(SWORD)
        )
        run_tick(
            world, GiveIntent(entity_id="ada", target_entity_id="cleo", kind=SWORD)
        )
        assert world.get_entity("ada").wielded == SWORD


class TestGiveInConversation:
    def test_participants_reach_each_other_across_the_anchor(
        self, world: World
    ) -> None:
        # ada (4,5) and bram (6,5) are two tiles apart, either side of (5,5).
        open_and_join(world)
        result = run_tick(
            world,
            GiveIntent(entity_id="ada", target_entity_id="bram", kind=STONE, amount=2),
        )
        assert give_results(result) == [(True, "gave 2 stone to bram")]
        assert world.get_entity("bram").inventory.count(STONE) == 2

    def test_giving_does_not_use_up_the_turn(self, world: World) -> None:
        conversation = open_and_join(world)
        run_tick(
            world, GiveIntent(entity_id="ada", target_entity_id="bram", kind=STONE)
        )
        updated = world.get_object(conversation.object_id)
        assert updated.get_state("speaker") == "ada"

    def test_a_bystander_two_tiles_away_is_out_of_reach(self, world: World) -> None:
        result = run_tick(
            world,
            GiveIntent(entity_id="ada", target_entity_id="bram", kind=STONE),
        )
        assert give_results(result) == [(False, "bram is not adjacent")]
        assert world.get_entity("bram").inventory.count(STONE) == 0


class TestGiveFailures:
    def test_unknown_target(self, world: World) -> None:
        result = run_tick(
            world,
            GiveIntent(entity_id="ada", target_entity_id="nobody", kind=STONE),
        )
        assert give_results(result) == [(False, "no entity nobody")]

    def test_giving_to_yourself(self, world: World) -> None:
        result = run_tick(
            world, GiveIntent(entity_id="ada", target_entity_id="ada", kind=STONE)
        )
        assert give_results(result) == [(False, "cannot give to yourself")]

    def test_not_enough_of_the_kind(self, world: World) -> None:
        result = run_tick(
            world,
            GiveIntent(entity_id="ada", target_entity_id="cleo", kind=STONE, amount=9),
        )
        assert give_results(result) == [(False, "not enough stone to give")]
        assert world.get_entity("ada").inventory.count(STONE) == 5

    def test_nonpositive_amount(self, world: World) -> None:
        result = run_tick(
            world,
            GiveIntent(entity_id="ada", target_entity_id="cleo", kind=STONE, amount=0),
        )
        assert give_results(result) == [(False, "amount must be positive")]

    def test_a_dead_target_takes_nothing(self, world: World) -> None:
        world.set_entity(world.get_entity("cleo").as_dead())
        result = run_tick(
            world, GiveIntent(entity_id="ada", target_entity_id="cleo", kind=STONE)
        )
        assert give_results(result) == [(False, "cleo is dead")]

    def test_a_wolf_takes_nothing(self, world: World) -> None:
        world.add_entity(
            Entity(
                entity_id="wolf_1",
                position=Position(x=3, y=5),
                entity_type="wolf",
            )
        )
        result = run_tick(
            world, GiveIntent(entity_id="ada", target_entity_id="wolf_1", kind=STONE)
        )
        assert give_results(result) == [(False, "cannot give to a wolf")]

    def test_a_dead_giver_is_skipped(self, world: World) -> None:
        world.set_entity(world.get_entity("ada").as_dead())
        ctx = TickContext(
            tick_id=world.tick, start_time_ms=0, deadline_ms=2**60, world=world
        )
        accepted, reason = ctx.submit_intent(
            "ada", GiveIntent(entity_id="ada", target_entity_id="cleo", kind=STONE)
        )
        assert not accepted
        assert reason == "dead"


class TestGiveMovesWithPositions:
    def test_reach_is_judged_after_movement(self, world: World) -> None:
        # dora (5,6) steps west to (4,6), next to ada (4,5), and is handed clay
        # on the same tick.
        result = run_tick(
            world,
            GiveIntent(entity_id="ada", target_entity_id="dora", kind=CLAY),
            MoveIntent(entity_id="dora", direction=Direction.WEST),
        )
        assert give_results(result) == [(True, "gave 1 clay to dora")]
