"""Tests for movement validation and conflict resolution."""

import pytest

from world.movement import MovementResolver, process_movement_phase
from world.state import Entity, Tile, World
from world.types import Direction, Position


class TestMoveValidation:
    """Tests for move validation phase."""

    def test_valid_cardinal_move(self, empty_world: World):
        """Entity can move to adjacent walkable tile."""
        empty_world.add_entity(
            Entity(entity_id="player1", position=Position(x=5, y=5))
        )
        resolver = MovementResolver(empty_world)

        claim = resolver.validate_move("player1", Direction.NORTH)

        assert claim is not None
        assert claim.from_pos == Position(x=5, y=5)
        assert claim.to_pos == Position(x=5, y=4)

    def test_valid_diagonal_move(self, empty_world: World):
        """Entity can make diagonal move when clear."""
        empty_world.add_entity(
            Entity(entity_id="player1", position=Position(x=5, y=5))
        )
        resolver = MovementResolver(empty_world)

        claim = resolver.validate_move("player1", Direction.NORTHEAST)

        assert claim is not None
        assert claim.to_pos == Position(x=6, y=4)

    def test_move_into_wall_rejected(self, world_with_walls: World):
        """Move into non-walkable tile is rejected."""
        world_with_walls.add_entity(
            Entity(entity_id="player1", position=Position(x=5, y=4))
        )
        resolver = MovementResolver(world_with_walls)

        claim = resolver.validate_move("player1", Direction.SOUTH)

        assert claim is None

    def test_move_out_of_bounds_rejected(self, empty_world: World):
        """Move outside world bounds is rejected."""
        empty_world.add_entity(
            Entity(entity_id="player1", position=Position(x=0, y=0))
        )
        resolver = MovementResolver(empty_world)

        claim = resolver.validate_move("player1", Direction.NORTH)

        assert claim is None

    def test_diagonal_move_blocked_when_north_blocked(self, world_with_l_wall: World):
        """Diagonal NE move fails if N is blocked."""
        # L-wall blocks (5, 4) and (6, 5)
        # Entity at (5, 5) cannot move NE because (5, 4) is blocked
        world_with_l_wall.add_entity(
            Entity(entity_id="player1", position=Position(x=5, y=5))
        )
        resolver = MovementResolver(world_with_l_wall)

        claim = resolver.validate_move("player1", Direction.NORTHEAST)

        assert claim is None

    def test_diagonal_move_blocked_when_east_blocked(self, empty_world: World):
        """Diagonal NE move fails if E is blocked."""
        empty_world.set_tile(
            Tile(position=Position(x=6, y=5), walkable=False)
        )
        empty_world.add_entity(
            Entity(entity_id="player1", position=Position(x=5, y=5))
        )
        resolver = MovementResolver(empty_world)

        claim = resolver.validate_move("player1", Direction.NORTHEAST)

        assert claim is None

    def test_diagonal_move_allowed_when_both_cardinals_clear(self, empty_world: World):
        """Diagonal move succeeds when both adjacent cardinals are walkable."""
        empty_world.add_entity(
            Entity(entity_id="player1", position=Position(x=5, y=5))
        )
        resolver = MovementResolver(empty_world)

        # All four diagonal directions should work
        for direction in [
            Direction.NORTHEAST,
            Direction.SOUTHEAST,
            Direction.SOUTHWEST,
            Direction.NORTHWEST,
        ]:
            claim = resolver.validate_move("player1", direction)
            assert claim is not None, f"{direction.name} should be allowed"


class TestConflictResolution:
    """Tests for conflict resolution phase."""

    def test_single_move_succeeds(self, empty_world: World):
        """Single move with no conflicts succeeds."""
        empty_world.add_entity(
            Entity(entity_id="player1", position=Position(x=5, y=5))
        )

        results = process_movement_phase(empty_world, {"player1": Direction.NORTH})

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].to_pos == Position(x=5, y=4)
        assert empty_world.get_entity("player1").position == Position(x=5, y=4)

    def test_two_independent_moves_succeed(self, two_entities: World):
        """Two entities moving to different destinations both succeed."""
        results = process_movement_phase(
            two_entities,
            {
                "entity_a": Direction.NORTH,
                "entity_b": Direction.SOUTH,
            },
        )

        assert len(results) == 2
        assert all(r.success for r in results)
        assert two_entities.get_entity("entity_a").position == Position(x=2, y=1)
        assert two_entities.get_entity("entity_b").position == Position(x=7, y=8)

    def test_same_destination_priority_winner(self, empty_world: World):
        """Lower entity_id wins when both claim same destination."""
        # Place two entities that will try to move to the same spot
        empty_world.add_entity(
            Entity(entity_id="alpha", position=Position(x=4, y=5))
        )
        empty_world.add_entity(
            Entity(entity_id="beta", position=Position(x=6, y=5))
        )

        # Both try to move to (5, 5)
        results = process_movement_phase(
            empty_world,
            {
                "alpha": Direction.EAST,
                "beta": Direction.WEST,
            },
        )

        # alpha should win (lexicographically first)
        alpha_result = next(r for r in results if r.entity_id == "alpha")
        beta_result = next(r for r in results if r.entity_id == "beta")

        assert alpha_result.success is True
        assert beta_result.success is False
        assert beta_result.failure_reason == "same_destination_conflict"
        assert empty_world.get_entity("alpha").position == Position(x=5, y=5)
        assert empty_world.get_entity("beta").position == Position(x=6, y=5)

    def test_swap_both_fail(self, adjacent_entities: World):
        """A->B and B->A swap: both entities stay in place."""
        # entity_a at (3, 3), entity_b at (4, 3)
        results = process_movement_phase(
            adjacent_entities,
            {
                "entity_a": Direction.EAST,  # a tries to move to b's position
                "entity_b": Direction.WEST,  # b tries to move to a's position
            },
        )

        # Both should fail
        assert len(results) == 2
        assert all(not r.success for r in results)
        assert all(r.failure_reason == "swap_conflict" for r in results)

        # Positions unchanged
        assert adjacent_entities.get_entity("entity_a").position == Position(x=3, y=3)
        assert adjacent_entities.get_entity("entity_b").position == Position(x=4, y=3)

    def test_cycle_all_fail(self, three_entities_triangle: World):
        """A->B->C->A cycle: all three stay in place."""
        # a at (3, 3), b at (4, 3), c at (4, 4)
        results = process_movement_phase(
            three_entities_triangle,
            {
                "a": Direction.EAST,  # a -> b's position (4, 3)
                "b": Direction.SOUTH,  # b -> c's position (4, 4)
                "c": Direction.NORTHWEST,  # c -> a's position (3, 3)
            },
        )

        # All should fail
        assert len(results) == 3
        assert all(not r.success for r in results)
        assert all(r.failure_reason == "cycle_conflict" for r in results)

        # All positions unchanged
        assert three_entities_triangle.get_entity("a").position == Position(x=3, y=3)
        assert three_entities_triangle.get_entity("b").position == Position(x=4, y=3)
        assert three_entities_triangle.get_entity("c").position == Position(x=4, y=4)

    def test_destination_occupied_by_non_mover(self, adjacent_entities: World):
        """Move into tile occupied by non-moving entity fails."""
        # entity_a at (3, 3), entity_b at (4, 3)
        results = process_movement_phase(
            adjacent_entities,
            {
                "entity_a": Direction.EAST,  # Try to move onto entity_b
                # entity_b does not submit intent
            },
        )

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].failure_reason == "destination_occupied"
        assert adjacent_entities.get_entity("entity_a").position == Position(x=3, y=3)

    def test_chain_both_succeed(self, adjacent_entities: World):
        """A->B, B->empty: A follows B, both succeed."""
        # entity_a at (3, 3), entity_b at (4, 3)
        results = process_movement_phase(
            adjacent_entities,
            {
                "entity_a": Direction.EAST,  # a wants b's spot
                "entity_b": Direction.EAST,  # b moves out of the way
            },
        )

        # Both should succeed
        assert len(results) == 2
        assert all(r.success for r in results)

        # a moved to where b was, b moved further east
        assert adjacent_entities.get_entity("entity_a").position == Position(x=4, y=3)
        assert adjacent_entities.get_entity("entity_b").position == Position(x=5, y=3)

    def test_invalid_move_not_included_in_results(self, world_with_walls: World):
        """Entity with invalid move is not in conflict resolution."""
        world_with_walls.add_entity(
            Entity(entity_id="player1", position=Position(x=5, y=4))
        )

        # Try to move into wall
        results = process_movement_phase(world_with_walls, {"player1": Direction.SOUTH})

        # Invalid move doesn't produce a result from conflict resolution
        assert len(results) == 0
        # Position unchanged
        assert world_with_walls.get_entity("player1").position == Position(x=5, y=4)

    def test_no_intents_empty_results(self, two_entities: World):
        """No intents produces empty results."""
        results = process_movement_phase(two_entities, {})
        assert results == []


class TestComplexScenarios:
    """Tests for complex multi-entity scenarios."""

    def test_three_way_same_destination(self, empty_world: World):
        """Three entities claiming same destination: lowest ID wins."""
        empty_world.add_entity(
            Entity(entity_id="charlie", position=Position(x=4, y=5))
        )
        empty_world.add_entity(
            Entity(entity_id="alpha", position=Position(x=5, y=4))
        )
        empty_world.add_entity(
            Entity(entity_id="bravo", position=Position(x=6, y=5))
        )

        # All try to move to (5, 5)
        results = process_movement_phase(
            empty_world,
            {
                "charlie": Direction.EAST,
                "alpha": Direction.SOUTH,
                "bravo": Direction.WEST,
            },
        )

        alpha_result = next(r for r in results if r.entity_id == "alpha")
        bravo_result = next(r for r in results if r.entity_id == "bravo")
        charlie_result = next(r for r in results if r.entity_id == "charlie")

        # alpha wins (lexicographically first)
        assert alpha_result.success is True
        assert bravo_result.success is False
        assert charlie_result.success is False

    def test_mixed_success_and_failure(self, empty_world: World):
        """Some entities succeed, some fail, independently."""
        empty_world.add_entity(Entity(entity_id="a", position=Position(x=2, y=2)))
        empty_world.add_entity(Entity(entity_id="b", position=Position(x=5, y=5)))
        empty_world.add_entity(Entity(entity_id="c", position=Position(x=8, y=8)))

        # Set up a wall that blocks c
        empty_world.set_tile(Tile(position=Position(x=8, y=7), walkable=False))

        results = process_movement_phase(
            empty_world,
            {
                "a": Direction.NORTH,  # succeeds
                "b": Direction.SOUTH,  # succeeds
                "c": Direction.NORTH,  # blocked by wall
            },
        )

        a_result = next(r for r in results if r.entity_id == "a")
        b_result = next(r for r in results if r.entity_id == "b")

        # a and b succeed
        assert a_result.success is True
        assert b_result.success is True

        # c has no result (invalid move filtered out)
        c_results = [r for r in results if r.entity_id == "c"]
        assert len(c_results) == 0

    def test_long_chain_succeeds(self, empty_world: World):
        """Long chain of entities all moving same direction succeeds."""
        # Create a line of 5 entities
        for i in range(5):
            empty_world.add_entity(
                Entity(entity_id=f"e{i}", position=Position(x=i + 2, y=5))
            )

        # All move east
        intents = {f"e{i}": Direction.EAST for i in range(5)}
        results = process_movement_phase(empty_world, intents)

        # All should succeed
        assert len(results) == 5
        assert all(r.success for r in results)

        # All moved one tile east
        for i in range(5):
            assert empty_world.get_entity(f"e{i}").position == Position(x=i + 3, y=5)

    def test_chain_preserves_position_index(self, empty_world: World):
        """Chain movement preserves position index for subsequent lookups.

        Regression test: without atomic position updates, a chain like
        A(1,1)->B(2,2), B(2,2)->C(3,3) would corrupt the position index
        because B's delete would remove A's newly added position.
        """
        # Set up chain: a at (2,2), b at (3,3)
        empty_world.add_entity(Entity(entity_id="a", position=Position(x=2, y=2)))
        empty_world.add_entity(Entity(entity_id="b", position=Position(x=3, y=3)))

        # a moves to b's position, b moves away
        results = process_movement_phase(
            empty_world,
            {
                "a": Direction.SOUTHEAST,  # (2,2) -> (3,3)
                "b": Direction.SOUTHEAST,  # (3,3) -> (4,4)
            },
        )

        # Both succeed
        assert len(results) == 2
        assert all(r.success for r in results)

        # Verify positions
        assert empty_world.get_entity("a").position == Position(x=3, y=3)
        assert empty_world.get_entity("b").position == Position(x=4, y=4)

        # Verify position index is correct (this was the bug!)
        assert empty_world.get_entity_at(Position(x=3, y=3)).entity_id == "a"
        assert empty_world.get_entity_at(Position(x=4, y=4)).entity_id == "b"
        assert empty_world.get_entity_at(Position(x=2, y=2)) is None

        # Verify a can move again (this would fail with corrupted index)
        results2 = process_movement_phase(
            empty_world,
            {"a": Direction.SOUTH},  # (3,3) -> (3,4)
        )
        assert len(results2) == 1
        assert results2[0].success is True
        assert empty_world.get_entity("a").position == Position(x=3, y=4)
