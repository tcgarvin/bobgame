"""Tests for tick loop."""

import asyncio
import time

import pytest

from world.state import Entity, World
from world.tick import TickConfig, TickContext, TickLoop, run_ticks
from world.types import Direction, Position


class TestTickContext:
    """Tests for TickContext class."""

    def test_submit_move_intent(self):
        """Can submit move intent before deadline."""
        ctx = TickContext(
            tick_id=0,
            start_time_ms=int(time.time() * 1000),
            deadline_ms=int(time.time() * 1000) + 10000,  # 10 seconds from now
        )

        result = ctx.submit_move_intent("player1", Direction.NORTH)

        assert result is True
        assert ctx.move_intents == {"player1": Direction.NORTH}

    def test_submit_duplicate_intent_rejected(self):
        """Cannot submit duplicate intent for same entity."""
        ctx = TickContext(
            tick_id=0,
            start_time_ms=int(time.time() * 1000),
            deadline_ms=int(time.time() * 1000) + 10000,
        )

        ctx.submit_move_intent("player1", Direction.NORTH)
        result = ctx.submit_move_intent("player1", Direction.SOUTH)

        assert result is False
        assert ctx.move_intents == {"player1": Direction.NORTH}

    def test_submit_after_deadline_rejected(self):
        """Cannot submit intent after deadline."""
        ctx = TickContext(
            tick_id=0,
            start_time_ms=int(time.time() * 1000) - 2000,  # 2 seconds ago
            deadline_ms=int(time.time() * 1000) - 1000,  # 1 second ago
        )

        result = ctx.submit_move_intent("player1", Direction.NORTH)

        assert result is False
        assert ctx.move_intents == {}

    def test_is_past_deadline(self):
        """Deadline check works correctly."""
        now = int(time.time() * 1000)

        # Future deadline
        ctx_future = TickContext(
            tick_id=0, start_time_ms=now, deadline_ms=now + 10000
        )
        assert ctx_future.is_past_deadline() is False

        # Past deadline
        ctx_past = TickContext(
            tick_id=0, start_time_ms=now - 2000, deadline_ms=now - 1000
        )
        assert ctx_past.is_past_deadline() is True


class TestRunTicks:
    """Tests for run_ticks helper function."""

    @pytest.mark.asyncio
    async def test_run_single_tick(self, empty_world: World):
        """Single tick with no intents."""
        results = await run_ticks(empty_world, num_ticks=1)

        assert len(results) == 1
        assert results[0].tick_id == 0
        assert results[0].move_results == []
        assert empty_world.tick == 1

    @pytest.mark.asyncio
    async def test_run_multiple_ticks(self, empty_world: World):
        """Multiple ticks advance tick counter."""
        results = await run_ticks(empty_world, num_ticks=5)

        assert len(results) == 5
        assert [r.tick_id for r in results] == [0, 1, 2, 3, 4]
        assert empty_world.tick == 5

    @pytest.mark.asyncio
    async def test_tick_with_move_intent(self, two_entities: World):
        """Submit move intent during tick."""

        async def submit_intents(ctx: TickContext):
            ctx.submit_move_intent("entity_a", Direction.NORTH)

        results = await run_ticks(two_entities, num_ticks=1, intent_callback=submit_intents)

        assert len(results) == 1
        assert len(results[0].move_results) == 1
        assert results[0].move_results[0].success is True
        assert two_entities.get_entity("entity_a").position == Position(x=2, y=1)

    @pytest.mark.asyncio
    async def test_multiple_ticks_with_intents(self, empty_world: World):
        """Submit intents over multiple ticks."""
        empty_world.add_entity(
            Entity(entity_id="walker", position=Position(x=5, y=5))
        )

        async def submit_intents(ctx: TickContext):
            # Move south each tick
            ctx.submit_move_intent("walker", Direction.SOUTH)

        results = await run_ticks(empty_world, num_ticks=3, intent_callback=submit_intents)

        assert len(results) == 3
        assert all(r.move_results[0].success for r in results)
        # Moved 3 tiles south
        assert empty_world.get_entity("walker").position == Position(x=5, y=8)

    @pytest.mark.asyncio
    async def test_entities_without_intent_stay(self, two_entities: World):
        """Entities that don't submit intent stay in place."""

        async def submit_intents(ctx: TickContext):
            ctx.submit_move_intent("entity_a", Direction.SOUTH)
            # entity_b submits nothing

        await run_ticks(two_entities, num_ticks=1, intent_callback=submit_intents)

        # entity_a moved, entity_b stayed
        assert two_entities.get_entity("entity_a").position == Position(x=2, y=3)
        assert two_entities.get_entity("entity_b").position == Position(x=7, y=7)


class TestTickLoop:
    """Tests for TickLoop class."""

    @pytest.mark.asyncio
    async def test_tick_loop_starts_and_stops(self, empty_world: World):
        """Tick loop can be started and stopped."""
        config = TickConfig(tick_duration_ms=50, intent_deadline_ms=25)
        loop = TickLoop(empty_world, config=config)

        assert loop.is_running is False

        # Start loop in background task
        task = asyncio.create_task(loop.run())

        # Wait a bit for loop to start
        await asyncio.sleep(0.01)
        assert loop.is_running is True

        # Stop the loop
        loop.stop()
        await task

        assert loop.is_running is False

    @pytest.mark.asyncio
    async def test_tick_loop_advances_ticks(self, empty_world: World):
        """Tick loop advances tick counter."""
        config = TickConfig(tick_duration_ms=30, intent_deadline_ms=15)
        loop = TickLoop(empty_world, config=config)

        task = asyncio.create_task(loop.run())

        # Wait for a few ticks
        await asyncio.sleep(0.1)

        loop.stop()
        await task

        # Should have advanced at least a couple ticks
        assert empty_world.tick >= 2

    @pytest.mark.asyncio
    async def test_submit_intent_to_loop(self, empty_world: World):
        """Can submit intent to running tick loop."""
        empty_world.add_entity(
            Entity(entity_id="player1", position=Position(x=5, y=5))
        )

        config = TickConfig(tick_duration_ms=100, intent_deadline_ms=50)
        results_received: list = []

        async def on_tick_complete(result):
            results_received.append(result)

        loop = TickLoop(empty_world, config=config, on_tick_complete=on_tick_complete)

        task = asyncio.create_task(loop.run())

        # Wait for tick to start
        await asyncio.sleep(0.01)

        # Submit intent
        accepted = loop.submit_move_intent("player1", Direction.NORTH)
        assert accepted is True

        # Wait for tick to complete
        await asyncio.sleep(0.15)

        loop.stop()
        await task

        # Should have received at least one result
        assert len(results_received) >= 1
        # Player should have moved
        assert empty_world.get_entity("player1").position.y < 5

    @pytest.mark.asyncio
    async def test_submit_intent_when_not_running_fails(self, empty_world: World):
        """Cannot submit intent when tick loop not running."""
        loop = TickLoop(empty_world)

        accepted = loop.submit_move_intent("player1", Direction.NORTH)

        assert accepted is False

    @pytest.mark.asyncio
    async def test_on_tick_complete_callback(self, empty_world: World):
        """on_tick_complete callback is called each tick."""
        config = TickConfig(tick_duration_ms=30, intent_deadline_ms=15)
        callback_count = 0

        async def on_tick_complete(result):
            nonlocal callback_count
            callback_count += 1

        loop = TickLoop(empty_world, config=config, on_tick_complete=on_tick_complete)

        task = asyncio.create_task(loop.run())
        await asyncio.sleep(0.1)
        loop.stop()
        await task

        assert callback_count >= 2

    @pytest.mark.asyncio
    async def test_current_context_available_during_tick(self, empty_world: World):
        """current_context is available while tick is in progress."""
        config = TickConfig(tick_duration_ms=100, intent_deadline_ms=50)
        loop = TickLoop(empty_world, config=config)

        # Not running yet
        assert loop.current_context is None

        task = asyncio.create_task(loop.run())
        await asyncio.sleep(0.01)

        # Should have context now
        assert loop.current_context is not None
        assert loop.current_context.tick_id == 0

        loop.stop()
        await task

        # Context cleared after stop
        assert loop.current_context is None


class TestTickConfig:
    """Tests for TickConfig class."""

    def test_default_config(self):
        """Default config has expected values."""
        config = TickConfig()
        assert config.tick_duration_ms == 1000
        assert config.intent_deadline_ms == 500

    def test_custom_config(self):
        """Can create custom config."""
        config = TickConfig(tick_duration_ms=500, intent_deadline_ms=200)
        assert config.tick_duration_ms == 500
        assert config.intent_deadline_ms == 200
