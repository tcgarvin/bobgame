"""Async tick loop for world simulation."""

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable, Mapping, TypeVar

import structlog

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
from .crafting import process_craft_phase
from .events import (
    ActionResult,
    DamageEvent,
    DeathEvent,
    EntityDespawnedEvent,
    EntitySpawnedEvent,
    ObjectAddedEvent,
    ObjectChange,
    ObjectRemovedEvent,
    RespawnEvent,
    TickEvents,
    UtteranceEvent,
)
from .foraging import (
    CollectResult,
    EatResult,
    process_collect_phase,
    process_eat_phase,
    process_extract_phase,
    process_regeneration,
)
from .moon import apply_new_moon
from .movement import MoveResult, process_movement_phase
from .sleep import process_fatigue_phase, process_sleep_phase
from .stats import (
    process_health_regen,
    process_food_phase,
    process_respawns,
    process_rest_phase,
)
from .state import WOLF_ENTITY_TYPE, World
from .tick_context import TickContext
from .types import (
    AttackIntent,
    ConverseIntent,
    CraftIntent,
    DepositIntent,
    Direction,
    DropIntent,
    EntityIntent,
    EquipIntent,
    ExtractIntent,
    GiveIntent,
    HEARING_RADIUS_BY_CHANNEL,
    PickupIntent,
    PlaceIntent,
    RestIntent,
    SAY_CHANNELS,
    SayIntent,
    SleepIntent,
    WaitIntent,
    WakeIntent,
    WithdrawIntent,
    WriteNoteIntent,
)
from .wolves import WolfSettings, WolfSimulator

if TYPE_CHECKING:
    # `save_coordinator` imports `snapshot`, which imports `recording`, which
    # imports this module; the tick loop only needs the type.
    from .save_coordinator import SaveCoordinator

logger = structlog.get_logger()

T = TypeVar("T", bound=EntityIntent)

__all__ = [
    "TickConfig",
    "TickContext",
    "TickLoop",
    "TickResult",
    "process_tick",
    "run_ticks",
]


@dataclass
class TickConfig:
    """Configuration for tick loop timing."""

    tick_duration_ms: int = 1000
    intent_deadline_ms: int = 500


@dataclass
class TickResult:
    """Result of a completed tick."""

    tick_id: int
    move_results: list[MoveResult]
    collect_results: list[CollectResult] = field(default_factory=list)
    eat_results: list[EatResult] = field(default_factory=list)
    object_changes: list[ObjectChange] = field(default_factory=list)
    duration_ms: float = 0.0

    # Extended per-tick events (see docs/05_jev_agents_design.md).
    # Every non-movement action (including collect/eat) is also reported here
    # as an ActionResult so services have one uniform list.
    action_results: list[ActionResult] = field(default_factory=list)
    damage_events: list[DamageEvent] = field(default_factory=list)
    deaths: list[DeathEvent] = field(default_factory=list)
    respawns: list[RespawnEvent] = field(default_factory=list)
    utterances: list[UtteranceEvent] = field(default_factory=list)
    objects_added: list[ObjectAddedEvent] = field(default_factory=list)
    objects_removed: list[ObjectRemovedEvent] = field(default_factory=list)
    entities_spawned: list[EntitySpawnedEvent] = field(default_factory=list)
    entities_despawned: list[EntityDespawnedEvent] = field(default_factory=list)


# Type alias for tick callbacks
TickCallback = Callable[[TickResult], Awaitable[None]]
TickStartCallback = Callable[[TickContext], Awaitable[None]]


# --- Tick pipeline --------------------------------------------------------


def _living_subset(
    world: World,
    intents: Mapping[str, T],
    action_type: str,
    events: TickEvents,
) -> dict[str, T]:
    """Drop intents from missing, dead or sleeping entities, recording why.

    `TickContext` already refuses a sleeper's intents; this is the same guard
    applied to intents the world itself injected (docs/10).
    """
    kept: dict[str, T] = {}
    for entity_id, intent in intents.items():
        entity = world.all_entities().get(entity_id)
        if entity is None:
            events.acted(entity_id, action_type, False, "entity not found")
            continue
        if not entity.alive:
            events.acted(entity_id, action_type, False, "dead")
            continue
        if entity.asleep:
            events.acted(entity_id, action_type, False, "asleep")
            continue
        kept[entity_id] = intent
    return kept


def _record_collect_results(results: list[CollectResult], events: TickEvents) -> None:
    for result in results:
        if result.success:
            events.acted(
                result.entity_id,
                "collect",
                True,
                f"collected {result.item_type} from {result.object_id}",
            )
        else:
            events.acted(
                result.entity_id, "collect", False, result.failure_reason or ""
            )


def _record_eat_results(results: list[EatResult], events: TickEvents) -> None:
    for result in results:
        if result.success:
            events.acted(
                result.entity_id,
                "eat",
                True,
                f"ate {result.amount} {result.item_type}",
            )
        else:
            events.acted(result.entity_id, "eat", False, result.failure_reason or "")


def _hearer_ids(world: World, entity_id: str, channel: str) -> list[str]:
    """Living, non-wolf settlers other than `entity_id` within the channel's
    earshot (`HEARING_RADIUS_BY_CHANNEL`), sorted for a stable report.

    Used only to tell the speaker who heard them (docs/09); observation
    filtering for what each listener actually receives happens independently
    in `services/observation_service.py`.
    """
    radius = HEARING_RADIUS_BY_CHANNEL.get(channel)
    if radius is None:
        return []
    speaker = world.get_entity(entity_id)
    heard = []
    for other_id, other in world.all_entities().items():
        if other_id == entity_id or not other.alive:
            continue
        if other.entity_type == WOLF_ENTITY_TYPE:
            continue
        if (
            abs(other.position.x - speaker.position.x) <= radius
            and abs(other.position.y - speaker.position.y) <= radius
        ):
            heard.append(other_id)
    return sorted(heard)


def _process_say_phase(
    world: World, intents: Mapping[str, SayIntent], events: TickEvents
) -> None:
    """Emit one utterance per speaker; channel filtering happens downstream.

    The action result's details carry who heard it (comma-separated entity
    ids, empty when nobody did) for `local` and `shout`, so the speaker's own
    tool result can say so; `thought` (viewer-only, no in-world hearers) keeps
    the plain channel name it always had.
    """
    for entity_id in sorted(intents):
        intent = intents[entity_id]
        if intent.channel not in SAY_CHANNELS:
            events.acted(entity_id, "say", False, f"unknown channel {intent.channel}")
            continue
        entity = world.get_entity(entity_id)
        events.utterances.append(
            UtteranceEvent(
                speaker_id=entity_id,
                channel=intent.channel,
                text=intent.text,
                position=entity.position,
            )
        )
        if intent.channel in HEARING_RADIUS_BY_CHANNEL:
            detail = (
                f"heard: {', '.join(_hearer_ids(world, entity_id, intent.channel))}"
            )
        else:
            detail = intent.channel
        events.acted(entity_id, "say", True, detail)


def process_tick(
    world: World,
    ctx: TickContext,
    wolf_simulator: WolfSimulator | None = None,
    regen_rate: int = 10,
) -> TickResult:
    """Run every phase of one tick against `world` and return its result.

    Phase order follows docs/05_jev_agents_design.md.
    """
    start = time.time()
    events = TickEvents()

    # Phase 0: wolves choose their intents (and spawn/despawn).
    if wolf_simulator is not None:
        wolf_simulator.step(world, ctx, events)

    # Phase 1: Movement
    move_intents = {
        entity_id: direction
        for entity_id, direction in ctx.move_intents.items()
        if (entity := world.all_entities().get(entity_id)) is not None
        and entity.alive
        and not entity.asleep
    }
    move_results = process_movement_phase(world, move_intents)

    # Phase 2: Attack
    process_attack_phase(
        world,
        _living_subset(world, ctx.intents_of(AttackIntent), "attack", events),
        events,
    )

    # Phase 3: Give, then conversations. Both run on post-combat positions;
    # give first, so a hand-over still works on the tick a conversation closes.
    process_give_phase(
        world,
        _living_subset(world, ctx.intents_of(GiveIntent), "give", events),
        events,
    )
    process_conversation_phase(
        world,
        _living_subset(world, ctx.intents_of(ConverseIntent), "converse", events),
        events,
    )

    # Phase 4: Extract
    process_extract_phase(
        world,
        _living_subset(world, ctx.intents_of(ExtractIntent), "extract", events),
        events,
    )

    # Phase 5: Collect, pickup, withdraw
    collect_results, collect_changes = process_collect_phase(
        world,
        _living_subset(world, ctx.collect_intents, "collect", events),
    )
    _record_collect_results(collect_results, events)
    events.object_changes.extend(collect_changes)

    process_pickup_phase(
        world,
        _living_subset(world, ctx.intents_of(PickupIntent), "pickup", events),
        events,
    )
    process_withdraw_phase(
        world,
        _living_subset(world, ctx.intents_of(WithdrawIntent), "withdraw", events),
        events,
    )

    # Phase 6: Drop, deposit
    process_drop_phase(
        world,
        _living_subset(world, ctx.intents_of(DropIntent), "drop", events),
        events,
    )
    process_deposit_phase(
        world,
        _living_subset(world, ctx.intents_of(DepositIntent), "deposit", events),
        events,
    )

    # Phase 7: Craft
    process_craft_phase(
        world,
        _living_subset(world, ctx.intents_of(CraftIntent), "craft", events),
        events,
    )

    # Phase 8: Equip
    process_equip_phase(
        world,
        _living_subset(world, ctx.intents_of(EquipIntent), "equip", events),
        events,
    )

    # Phase 9: Place
    process_place_phase(
        world,
        _living_subset(world, ctx.intents_of(PlaceIntent), "place", events),
        events,
    )

    # Phase 10: Write note
    process_write_note_phase(
        world,
        _living_subset(world, ctx.intents_of(WriteNoteIntent), "write_note", events),
        events,
    )

    # Phase 11: Eat, rest, say
    eat_results = process_eat_phase(
        world, _living_subset(world, ctx.eat_intents, "eat", events)
    )
    _record_eat_results(eat_results, events)
    process_rest_phase(
        world,
        _living_subset(world, ctx.intents_of(RestIntent), "rest", events),
        events,
    )
    _process_say_phase(
        world,
        _living_subset(world, ctx.intents_of(SayIntent), "say", events),
        events,
    )

    # Phase 12: Wait
    for entity_id in sorted(
        _living_subset(world, ctx.intents_of(WaitIntent), "wait", events)
    ):
        events.acted(entity_id, "wait", True, "")

    # Phase 13: Sleep and wake, after movement and every action phase.
    process_sleep_phase(
        world,
        _living_subset(world, ctx.intents_of(SleepIntent), "sleep", events),
        ctx.intents_of(WakeIntent),
        events,
    )

    # Phase 13b: the new moon, once a run of days (docs/14). On the first tick
    # of a new-moon night everyone left awake lies down and every conversation
    # ends.
    apply_new_moon(world, events)

    # Phase 14: Bookkeeping
    process_food_phase(world, events)
    process_fatigue_phase(world, events)
    process_health_regen(world)
    events.object_changes.extend(process_regeneration(world, regen_rate=regen_rate))
    process_respawns(world, events)

    elapsed_ms = (time.time() - start) * 1000

    return TickResult(
        tick_id=ctx.tick_id,
        move_results=move_results,
        collect_results=collect_results,
        eat_results=eat_results,
        object_changes=events.object_changes,
        duration_ms=elapsed_ms,
        action_results=events.action_results,
        damage_events=events.damage_events,
        deaths=events.deaths,
        respawns=events.respawns,
        utterances=events.utterances,
        objects_added=events.objects_added,
        objects_removed=events.objects_removed,
        entities_spawned=events.entities_spawned,
        entities_despawned=events.entities_despawned,
    )


class TickLoop:
    """
    Async tick loop for world simulation.

    Usage:
        world = World(width=100, height=100)
        loop = TickLoop(world)

        # In gRPC handler or test:
        loop.submit_move_intent(entity_id, direction)

        # Start the loop
        await loop.run()
    """

    def __init__(
        self,
        world: World,
        config: TickConfig | None = None,
        on_tick_complete: TickCallback | None = None,
        on_tick_start: TickStartCallback | None = None,
        wolves_enabled: bool = False,
        wolf_seed: int = 1337,
        wolf_settings: WolfSettings = WolfSettings(),
        save_coordinator: "SaveCoordinator | None" = None,
    ):
        self.world = world
        self.config = config or TickConfig()
        self.on_tick_complete = on_tick_complete
        self.on_tick_start = on_tick_start
        self.wolves_enabled = wolves_enabled
        self.wolf_simulator = WolfSimulator(seed=wolf_seed, settings=wolf_settings)
        # None means this world never saves (tests, and a run with no run dir).
        self.save_coordinator: "SaveCoordinator | None" = save_coordinator

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

    def submit_intent(self, entity_id: str, intent: EntityIntent) -> tuple[bool, str]:
        """Submit any intent for the current tick.

        Returns (accepted, reason); the reason is `no_tick_in_progress` when no
        tick is running.
        """
        if self._current_context is None:
            logger.warning("intent_rejected_no_tick", entity_id=entity_id)
            return False, "no_tick_in_progress"
        return self._current_context.submit_intent(entity_id, intent)

    async def run(self) -> None:
        """Run the tick loop until stopped."""
        self._running = True
        self._stop_event.clear()

        logger.info("tick_loop_started", tick_duration_ms=self.config.tick_duration_ms)

        try:
            while self._running:
                tick_start = time.time() * 1000

                # A new-moon save pauses this tick between the observations
                # and the intent window (docs/14); the directory has to exist
                # before the observations go out.
                save_tick = (
                    self.save_coordinator.pending_save_tick()
                    if self.save_coordinator is not None
                    else 0
                )

                # Create context for this tick
                self._current_context = TickContext(
                    tick_id=self.world.tick,
                    start_time_ms=int(tick_start),
                    deadline_ms=int(tick_start + self.config.intent_deadline_ms),
                    world=self.world,
                    save_tick=save_tick,
                )

                logger.debug("tick_started", tick_id=self._current_context.tick_id)

                if save_tick and self.save_coordinator is not None:
                    self.save_coordinator.prepare(save_tick)

                # Notify tick start (for sending observations)
                if self.on_tick_start:
                    await self.on_tick_start(self._current_context)

                if save_tick and self.save_coordinator is not None:
                    await self.save_coordinator.wait_and_write(save_tick)
                    self._restart_deadline()

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

        except Exception:
            # The task's exception is otherwise only seen when the server is
            # stopped, with the frames that matter stripped; log it here.
            logger.exception("tick_loop_crashed", tick=self.world.tick)
            raise
        finally:
            self._running = False
            self._current_context = None
            logger.info("tick_loop_stopped")

    def _restart_deadline(self) -> None:
        """Give this tick a fresh intent window after a save's pause.

        The deadline was set when the tick began; a save takes as long as the
        settlers need, so without this every intent for the save tick would be
        rejected as late and the tick would not run normally (docs/14).
        """
        if self._current_context is None:
            return
        self._current_context.deadline_ms = int(
            time.time() * 1000 + self.config.intent_deadline_ms
        )

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

        result = process_tick(
            self.world,
            ctx,
            self.wolf_simulator if self.wolves_enabled else None,
        )

        logger.debug(
            "tick_processed",
            tick_id=ctx.tick_id,
            intents_submitted=len(ctx.intents),
            moves_succeeded=sum(1 for r in result.move_results if r.success),
            actions=len(result.action_results),
            duration_ms=result.duration_ms,
        )
        return result

    def stop(self) -> None:
        """Signal the tick loop to stop."""
        self._running = False
        self._stop_event.set()


async def run_ticks(
    world: World,
    num_ticks: int,
    intent_callback: Callable[[TickContext], Awaitable[None]] | None = None,
    config: TickConfig | None = None,
    wolf_simulator: WolfSimulator | None = None,
) -> list[TickResult]:
    """
    Run a fixed number of ticks (useful for testing).

    Args:
        world: World state
        num_ticks: Number of ticks to run
        intent_callback: Optional async callback to submit intents each tick
        config: Tick timing configuration
        wolf_simulator: Optional wolf simulation (disabled when None)

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
            world=world,
        )

        # Allow test to submit intents
        if intent_callback:
            await intent_callback(ctx)

        results.append(process_tick(world, ctx, wolf_simulator))
        world.advance_tick()

    return results
