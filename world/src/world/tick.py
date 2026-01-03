"""Async tick loop for world simulation."""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import structlog

from .movement import MoveResult, process_movement_phase
from .state import World
from .types import Direction

logger = structlog.get_logger()


@dataclass
class TickConfig:
    """Configuration for tick loop timing."""

    tick_duration_ms: int = 1000
    intent_deadline_ms: int = 500


@dataclass
class TickContext:
    """Context for a single tick."""

    tick_id: int
    start_time_ms: int
    deadline_ms: int

    # Collected intents for this tick
    move_intents: dict[str, Direction] = field(default_factory=dict)

    def is_past_deadline(self) -> bool:
        """Check if current time is past the intent deadline."""
        return time.time() * 1000 > self.deadline_ms

    def submit_move_intent(self, entity_id: str, direction: Direction) -> bool:
        """
        Submit a move intent for this tick.
        Returns True if accepted, False if past deadline or already submitted.
        """
        if self.is_past_deadline():
            logger.debug(
                "intent_rejected_late", entity_id=entity_id, tick_id=self.tick_id
            )
            return False
        if entity_id in self.move_intents:
            logger.debug(
                "intent_rejected_duplicate", entity_id=entity_id, tick_id=self.tick_id
            )
            return False
        self.move_intents[entity_id] = direction
        return True


@dataclass
class TickResult:
    """Result of a completed tick."""

    tick_id: int
    move_results: list[MoveResult]
    duration_ms: float


# Type alias for tick callbacks
TickCallback = Callable[[TickResult], Awaitable[None]]


class TickLoop:
    """
    Async tick loop for world simulation.

    Usage:
        world = World(width=100, height=100)
        loop = TickLoop(world)

        # In gRPC handler or test:
        loop.submit_intent(entity_id, direction)

        # Start the loop
        await loop.run()
    """

    def __init__(
        self,
        world: World,
        config: TickConfig | None = None,
        on_tick_complete: TickCallback | None = None,
    ):
        self.world = world
        self.config = config or TickConfig()
        self.on_tick_complete = on_tick_complete

        self._running = False
        self._current_context: TickContext | None = None
        self._stop_event = asyncio.Event()

    @property
    def current_tick(self) -> int:
        """Current tick ID."""
        return self.world.tick

    @property
    def current_context(self) -> TickContext | None:
        """Current tick context, if tick is in progress."""
        return self._current_context

    @property
    def is_running(self) -> bool:
        """Whether the tick loop is currently running."""
        return self._running

    def submit_move_intent(self, entity_id: str, direction: Direction) -> bool:
        """
        Submit a move intent for the current tick.

        Thread-safe when called from async context.
        Returns False if no tick in progress or past deadline.
        """
        if self._current_context is None:
            logger.warning("intent_rejected_no_tick", entity_id=entity_id)
            return False
        return self._current_context.submit_move_intent(entity_id, direction)

    async def run(self) -> None:
        """Run the tick loop until stopped."""
        self._running = True
        self._stop_event.clear()

        logger.info("tick_loop_started", tick_duration_ms=self.config.tick_duration_ms)

        try:
            while self._running:
                tick_start = time.time() * 1000

                # Create context for this tick
                self._current_context = TickContext(
                    tick_id=self.world.tick,
                    start_time_ms=int(tick_start),
                    deadline_ms=int(tick_start + self.config.intent_deadline_ms),
                )

                logger.debug("tick_started", tick_id=self._current_context.tick_id)

                # Wait for intent deadline
                await self._wait_until_deadline()

                # Process tick
                result = self._process_tick()

                # Callback
                if self.on_tick_complete:
                    await self.on_tick_complete(result)

                # Advance world tick
                self.world.advance_tick()

                # Wait for remainder of tick duration
                elapsed = time.time() * 1000 - tick_start
                remaining = self.config.tick_duration_ms - elapsed
                if remaining > 0:
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(), timeout=remaining / 1000
                        )
                    except asyncio.TimeoutError:
                        pass  # Normal - tick duration elapsed

        finally:
            self._running = False
            self._current_context = None
            logger.info("tick_loop_stopped")

    async def _wait_until_deadline(self) -> None:
        """Wait until the intent deadline."""
        if self._current_context is None:
            return

        now = time.time() * 1000
        wait_ms = self._current_context.deadline_ms - now
        if wait_ms > 0:
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=wait_ms / 1000)
            except asyncio.TimeoutError:
                pass  # Normal - deadline reached

    def _process_tick(self) -> TickResult:
        """Process the current tick and return results."""
        ctx = self._current_context
        if ctx is None:
            raise RuntimeError("No tick context")

        start = time.time()

        # Process movement phase
        move_results = process_movement_phase(self.world, ctx.move_intents)

        elapsed_ms = (time.time() - start) * 1000

        logger.debug(
            "tick_processed",
            tick_id=ctx.tick_id,
            moves_submitted=len(ctx.move_intents),
            moves_succeeded=sum(1 for r in move_results if r.success),
            duration_ms=elapsed_ms,
        )

        return TickResult(
            tick_id=ctx.tick_id,
            move_results=move_results,
            duration_ms=elapsed_ms,
        )

    def stop(self) -> None:
        """Signal the tick loop to stop."""
        self._running = False
        self._stop_event.set()


async def run_ticks(
    world: World,
    num_ticks: int,
    intent_callback: Callable[[TickContext], Awaitable[None]] | None = None,
    config: TickConfig | None = None,
) -> list[TickResult]:
    """
    Run a fixed number of ticks (useful for testing).

    Args:
        world: World state
        num_ticks: Number of ticks to run
        intent_callback: Optional async callback to submit intents each tick
        config: Tick timing configuration

    Returns:
        List of TickResults
    """
    config = config or TickConfig()
    results: list[TickResult] = []

    for _ in range(num_ticks):
        tick_start = time.time() * 1000

        ctx = TickContext(
            tick_id=world.tick,
            start_time_ms=int(tick_start),
            deadline_ms=int(tick_start + config.intent_deadline_ms),
        )

        # Allow test to submit intents
        if intent_callback:
            await intent_callback(ctx)

        # Process movement
        move_results = process_movement_phase(world, ctx.move_intents)

        elapsed_ms = (time.time() - tick_start) * 1000

        result = TickResult(
            tick_id=ctx.tick_id,
            move_results=move_results,
            duration_ms=elapsed_ms,
        )
        results.append(result)

        world.advance_tick()

    return results
