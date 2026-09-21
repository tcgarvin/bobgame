"""The agent process: one tick loop, one planner task, one entity.

The tick loop owns the world connection and submits exactly one intent per tick,
before the deadline. The planner runs as a background task and reaches the tick
loop only through the handshakes on `JevAgent` - `run_stint`, `direct_action`,
`wait_ticks`, `open_conversation` and `join_conversation` - each of which parks
the planner on an asyncio Future until the tick loop has actually done the
thing.

`JevAgent.mode` is the whole state machine (docs/09 section 4.1):

    planning      the planner is thinking; the body waits, acts once, or speaks
    stint         Jev (or a code driver) holds the controls
    reflex        the pre-registered reflex brief has fired and holds them
    conversation  the actor holds a seat and answers on its own turn

A reflex pre-empts planning, conversation and driver stints, never an ordinary
Jev stint. The decisions themselves live in `reflex.py` and `conversation.py`;
this module only sequences them.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from pathlib import Path
from typing import Any, Callable, Coroutine, Sequence

import grpc
import structlog

from ... import world_pb2 as pb
from ..client import WorldClient
from ..items import DEFAULT_SETTLER_COUNT
from ..conversation import (
    ACTION_HAIL,
    ACTION_JOIN,
    ACTION_OPEN,
    UNASKED_VIA,
    ConversationReport,
    ConversationSession,
    Converser,
    ModelConverser,
    joined_conversation,
    sleep_end_reason,
)
from ..jevclient import JevClient, TypeSafeJevClient
from ..outcomes import ActionOutcome, Seat
from ..journal import (
    JOURNAL_WAIT_SECONDS,
    KIND_EVENT,
    KIND_NOTE,
    TRIGGER_DEATH,
    TRIGGER_SLEEP,
    DayLogEntry,
    Journal,
    JournalWriter,
    ModelJournalWriter,
    render_day_log,
    rewrite_duration_ms,
)
from ..planner import (
    DEATH_NOTE,
    SLEEP_NOTE,
    Planner,
    alert_window_start,
    threat_alert,
)
from ..reflex import (
    EMPTY_REFLEX,
    INTERRUPTED_CONVERSATION,
    INTERRUPTED_DRIVER_STINT,
    INTERRUPTED_PLANNING,
    ReflexBrief,
    ReflexStore,
    ReflexWatch,
    reflex_report_line,
)
from ..snapshot import (
    AgentSnapshot,
    SleepSnapshot,
    capture,
    read_snapshot,
    restore,
    snapshot_path,
    write_snapshot,
)
from ..briefs import (
    INTERRUPTED_BY_REFLEX,
    Brief,
    StintDriver,
    conversation_interruption,
)
from ..stint import (
    END_ASLEEP,
    END_DEATH,
    END_JOINED_CONVERSATION,
    END_NEW_MOON,
    END_PREEMPTED_BY_REFLEX,
    STINT_KIND_REFLEX,
    Stint,
    StintReport,
    never_ends,
    stint_stats,
)
from ..pricing import CostLedger, LedgerJevClient
from ..tracelog import RUN_DIR_ENV, AgentTrace, resolve_log_root
from ..worldmodel import TickDigest, WorldModel
from .modes import (
    CONVERSATION_START_GRACE_TICKS,
    LIFE_RESPAWNED,
    LIFE_WOKE,
    MAX_PLANNER_WAIT_MS,
    MODE_CONVERSATION,
    MODE_IDLE,
    MODE_PLANNING,
    MODE_REFLEX,
    MODE_STINT,
    PLANNER_POLL_SECONDS,
    SUBMIT_MARGIN_MS,
    THOUGHT_CHANNEL,
    USE_RUN_DIR,
    ResultSink,
)
from .requests import (
    ASLEEP_REJECTION,
    _ConversationWaiter,
    _DirectRequest,
    _HeldStint,
    _StintRequest,
    _drain,
)
from .saving import (
    DRAINED,
    SAVES_DIR_NAME,
    SAVE_POLL_SECONDS,
    DrainState,
    save_wait_seconds,
)
from .sleeping import (
    GROUND_SLEEP_PLACE,
    SLEEP_START_GRACE_TICKS,
    WAKE_ACTION,
    SleepRecord,
    WakeWaiters,
    sleep_place,
    wake_reason,
)
from .status import StatusLine, _write_pricing

logger = structlog.get_logger(__name__)


class JevAgent:
    """A planner and a Jev executor sharing one entity and one tick loop."""

    def __init__(
        self,
        world: WorldClient,
        jev: JevClient,
        entity_id: str,
        *,
        log_root: Path = USE_RUN_DIR,
        planner_model: str = "",
        journal_model: str = "",
        converser: Converser | None = None,
        journal_writer: JournalWriter | None = None,
        settler_count: int = DEFAULT_SETTLER_COUNT,
    ) -> None:
        self.world = world
        self.ledger = CostLedger()
        # Wrapped once here, so every Jev call - ordinary stints, reflex stints
        # and conversation sessions - is billed to this agent's ledger.
        self.jev: JevClient = LedgerJevClient(jev, self.ledger)
        self.entity_id = entity_id
        self.log_root = resolve_log_root() if log_root == USE_RUN_DIR else log_root
        self.trace = AgentTrace(entity_id, self.log_root)
        self._model = WorldModel(entity_id)

        self.mode = MODE_IDLE
        self.settler_count = settler_count
        self.planner = Planner(
            self,
            entity_id,
            model_name=planner_model,
            trace=self.trace,
            ledger=self.ledger,
            settler_count=settler_count,
        )
        self.converser: Converser = (
            ModelConverser(planner_model, self.ledger, settler_count)
            if converser is None
            else converser
        )
        self.journal_writer: JournalWriter = (
            ModelJournalWriter(
                journal_model,
                self.planner.model_name,
                settler_count=settler_count,
            )
            if journal_writer is None
            else journal_writer
        )

        _write_pricing(self.trace, self.planner.model_name)

        self.reflex_store = ReflexStore(self.trace.reflex_path)
        self._reflex_watch = ReflexWatch(self.reflex_store.load())
        self._reflex_stint: Stint | None = None
        self._reflex_start_tick = 0
        self._reflex_start_health = 0
        # Reflex lines and conversation reports the planner has not been shown
        # yet; one queue for the next tool result, one for the next prompt.
        self._notes_for_tools: list[str] = []
        self._notes_for_prompt: list[str] = []

        self._stint_requests: asyncio.Queue[_StintRequest] = asyncio.Queue()
        self._direct_requests: asyncio.Queue[_DirectRequest] = asyncio.Queue()
        self._active_stint: Stint | None = None
        self._active_request: _StintRequest | None = None
        self._awaiting_direct: _DirectRequest | None = None
        # Absolute wall-clock ms by which the world wants this tick's intent,
        # as the observation reports it. 0.0 until the first observation.
        self._deadline_ms = 0.0
        self._held_stint: _HeldStint | None = None
        self._conversation: ConversationSession | None = None
        self._conversation_waiters: list[_ConversationWaiter] = []
        # -1 means awake; otherwise the tick the current sleep began.
        self._asleep_since = -1
        self._sleep_fatigue_before = 0
        self._sleep_place = GROUND_SLEEP_PLACE
        self._last_sleep: SleepRecord | None = None
        self._wake_waiters = WakeWaiters()
        # Planner turns parked until the body is awake and alive again.
        self._active_waiters: list[asyncio.Future[None]] = []
        self._background: set[asyncio.Task[None]] = set()
        # The journal rewrite in flight, if any: exactly one at a time.
        self._journal_task: asyncio.Task[None] | None = None
        # Closing converser calls still running, counted rather than held: the
        # save waits for them (docs/14 section 2).
        self._closing_conversations = 0
        # True once the sleep the body is in has been drained through, so the
        # drain runs once per sleep rather than once per sleeping tick.
        self._drained_this_sleep = False
        self._pending_thought = ""
        self._last_status = StatusLine()
        self._running = False
        # Set by the planner's `open_conversation`/`talk_to` just before the
        # open or hail intent, and read once by `_begin_conversation` when that
        # actor's own seat lands (docs/09 section 10, item 4).
        self._pending_purpose = ""

    # -- AgentBridge --------------------------------------------------------

    @property
    def model(self) -> WorldModel:
        """The shared world model, updated at the top of every tick."""
        return self._model

    @property
    def reflex(self) -> ReflexBrief:
        """The brief code runs when a wolf is close or the actor is bitten."""
        return self._reflex_watch.brief

    @property
    def reflex_watch(self) -> ReflexWatch:
        """The reflex trigger and its counters, which a snapshot carries."""
        return self._reflex_watch

    def set_reflex(self, brief: ReflexBrief) -> None:
        """Register (or replace) the reflex brief and persist it."""
        self._reflex_watch.set_brief(brief)
        self.reflex_store.save(brief)
        logger.info("reflex_registered", instruction=brief.instruction)

    def clear_reflex(self) -> None:
        """Forget the reflex brief; nothing fires until a new one is set."""
        self._reflex_watch.set_brief(EMPTY_REFLEX)
        self.reflex_store.save(EMPTY_REFLEX)

    def set_conversation_purpose(self, purpose: str) -> None:
        """Remember why the open/hail about to be submitted was started."""
        self._pending_purpose = purpose

    def drain_notes(self, *, for_prompt: bool = False) -> list[str]:
        """Reflex lines and conversation reports not yet shown, emptied as taken.

        There are two queues over one source, because the contract shows each
        note both in the next tool result and in the next turn prompt.
        """
        queue = self._notes_for_prompt if for_prompt else self._notes_for_tools
        notes = list(queue)
        queue.clear()
        return notes

    def pending_notes(self, *, for_prompt: bool = False) -> list[str]:
        """The same notes, read without taking them: a snapshot must not consume."""
        return list(self._notes_for_prompt if for_prompt else self._notes_for_tools)

    def _note_for_planner(self, text: str) -> None:
        """Queue one note for the next tool result and the next turn prompt."""
        if not text:
            return
        self._notes_for_tools.append(text)
        self._notes_for_prompt.append(text)
        # Recorded here rather than where the planner drains it, because there
        # are two queues over one note and the journal wants it once.
        self.planner.day_log.add(self._model.tick, KIND_NOTE, text)

    def inactive_reason(self) -> str:
        """Why the body can take no action at all right now, or `""`.

        A sleeping, collapsed or dead body submits nothing, so a request made
        while it is in that state can never run. It is refused here rather
        than queued: a multi-phase tool (`build`, a craft loop, `talk_to`'s
        walk-then-hail) keeps calling back after `_drain_for_sleep` has swept
        the queues once, and a request that arrives afterwards would sit there
        all night and make `drained()` answer "a stint is queued", which
        abandons the whole save (docs/14).
        """
        info = self._model.self_info
        if not info.alive:
            return END_DEATH
        if not info.asleep:
            return ""
        return END_NEW_MOON if self._model.clock.new_moon_tonight else END_ASLEEP

    def _refused_report(self, brief: Brief, reason: str) -> StintReport:
        """A stint that never ran, rendered as a finished one of zero ticks."""
        position = self._model.position
        stats = stint_stats(self._model)
        return StintReport(
            brief=brief,
            ticks_used=0,
            end_reason=reason,
            start_position=position,
            end_position=position,
            start_stats=stats,
            end_stats=stats,
            inventory_delta={},
            action_counts={},
            notable=[],
            tail=[],
        )

    async def run_stint(
        self,
        brief: Brief,
        driver: StintDriver | None = None,
        end_check: Callable[[WorldModel], str] = never_ends,
    ) -> StintReport:
        """Queue a stint and wait for the tick loop to finish running it.

        With a `driver`, code chooses the action every tick and Jev is not
        called at all; that is how the planner's `build` tool works.
        `end_check` is asked before every tick and ends the stint with the
        reason it returns.
        """
        inactive = self.inactive_reason()
        if inactive:
            return self._refused_report(brief, inactive)
        future: asyncio.Future[StintReport] = asyncio.get_running_loop().create_future()
        await self._stint_requests.put(
            _StintRequest(
                brief=brief, future=future, driver=driver, end_check=end_check
            )
        )
        return await future

    async def direct_action(self, intent: pb.Intent, description: str) -> ActionOutcome:
        """Submit one intent on the next tick and report what the world did."""
        if self.inactive_reason():
            return ActionOutcome.never_ran(description, ASLEEP_REJECTION)
        if self._reflex_stint is not None:
            return ActionOutcome.never_ran(description, INTERRUPTED_BY_REFLEX)
        session = self._conversation
        if session is not None:
            return ActionOutcome.never_ran(
                description, conversation_interruption(session.conversation_id)
            )
        future: asyncio.Future[ActionOutcome] = (
            asyncio.get_running_loop().create_future()
        )
        await self._direct_requests.put(
            _DirectRequest(intent=intent, description=description, future=future)
        )
        return await future

    async def wait_ticks(self, ticks: int) -> str:
        """Hold position for `ticks` ticks."""
        if self.inactive_reason():
            return f"wait {ticks} ticks -> {ASLEEP_REJECTION}"
        future: asyncio.Future[ActionOutcome] = (
            asyncio.get_running_loop().create_future()
        )
        await self._direct_requests.put(
            _DirectRequest(
                intent=pb.Intent(wait=pb.WaitIntent()),
                description=f"wait {ticks} ticks",
                future=future,
                remaining_ticks=max(1, ticks),
            )
        )
        return (await future).text()

    async def await_conversation(self) -> ConversationReport | None:
        """Block until the conversation now starting has ended.

        None means the tick loop never saw the actor take a seat, so there is
        nothing to wait for.
        """
        if self.inactive_reason():
            return None
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ConversationReport | None] = loop.create_future()
        waiter = _ConversationWaiter(
            future=future,
            deadline_tick=self._model.tick + CONVERSATION_START_GRACE_TICKS,
        )
        self._conversation_waiters.append(waiter)
        return await waiter.future

    async def await_wake(self, since_tick: int) -> str:
        """Block until the actor, asleep since `since_tick`, has woken.

        Returns the sleep as one line, or `""` when the tick loop never saw the
        actor asleep, so there was no sleep to sit through.
        """
        last = self._last_sleep
        if self._asleep_since < 0 and last is not None and last.end_tick >= since_tick:
            return last.to_text()
        return await self._wake_waiters.park(self._model.tick + SLEEP_START_GRACE_TICKS)

    async def await_active(self) -> None:
        """Block until the body is awake and alive; return at once if it is.

        The planner takes no turn while the settler is asleep, collapsed or
        waiting to respawn: there is nothing it could do, and every call it
        made would be refused (docs/12_sleep_journal.md).
        """
        if self._body_is_active():
            return
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._active_waiters.append(future)
        await future

    def _body_is_active(self) -> bool:
        info = self._model.self_info
        return info.alive and not info.asleep

    def _release_active_waiters(self) -> None:
        for future in self._active_waiters:
            if not future.done():
                future.set_result(None)
        self._active_waiters.clear()

    async def await_journal(self) -> None:
        """Wait for a journal rewrite in flight, then let the turn read the file.

        Bounded: a rewrite that has not answered in `JOURNAL_WAIT_SECONDS` is
        left running and the turn goes ahead with the journal as it stands,
        because a planner that never takes a turn is worse than a stale note.
        """
        task = self._journal_task
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), JOURNAL_WAIT_SECONDS)
        except asyncio.TimeoutError:
            logger.warning(
                "journal_rewrite_slow",
                entity_id=self.entity_id,
                waited_seconds=JOURNAL_WAIT_SECONDS,
            )

    def set_thought(self, thought: str) -> None:
        """Queue a planner reflection to be spoken on the `thought` channel."""
        self._pending_thought = thought

    # -- tick loop ----------------------------------------------------------

    async def run(self) -> None:
        """Connect, then drive the tick loop until the stream ends or we stop."""
        await self.world.acquire_lease()
        self._running = True
        self.mode = MODE_PLANNING

        renewal = asyncio.create_task(self.world.run_lease_renewal())
        planner_task = asyncio.create_task(self.planner.run())
        try:
            async for observation in self.world.observations():
                if not self._running:
                    break
                await self._handle_tick(observation)
        except grpc.RpcError as error:
            logger.error("observation_stream_failed", details=error.details())
        finally:
            self._running = False
            for task in (planner_task, renewal):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            self._fail_pending("agent stopped")

    def stop(self) -> None:
        """Ask the tick loop to finish after the current tick."""
        self._running = False

    async def _handle_tick(self, observation: pb.Observation) -> None:
        digest = self._model.update(observation)
        self._deadline_ms = float(observation.deadline_ms)
        # A sign pushes its line at whoever walks past, so the planner is told
        # through the same note path as a reflex line (docs/08_building.md).
        for note in digest.sign_notes:
            self._note_for_planner(note)
        for note in digest.board_notes:
            self._note_for_planner(note)
        # A re-delivered tick is one this settler has already lived through
        # (docs/14 section 4): its deaths, respawns and sleeps are history.
        if not digest.repeated:
            self._note_life_transitions(digest)
            self._note_sleep_transitions(digest)
        if self._body_is_active():
            self._release_active_waiters()
        clock = self._model.clock
        if clock.save_tick and clock.save_tick == self._model.tick:
            await self._maybe_save(clock.save_tick)
        if self._model.self_info.asleep:
            await self._sleeping_tick(observation, digest)
            return
        self._wake_waiters.expire(self._model.tick)
        # The reflex is checked before anything else: a single-tick action in
        # flight is answered as interrupted rather than with its own outcome.
        self._maybe_start_reflex(digest)
        # Before the single-tick action is resolved, for the same reason as the
        # reflex: a seat taken without asking answers it as interrupted rather
        # than handing it the conversation's own event.
        self._detect_join(digest)
        self._resolve_awaiting_direct(digest)
        self._expire_conversation_waiters()

        intent, sink = await self._choose_intent(digest)

        if not self._model.self_info.alive:
            # The world rejects every intent from a dead entity; stay quiet
            # until the respawn instead of logging a rejection per tick.
            if sink is not None:
                sink("rejected: dead")
            await self._report_status()
            return

        try:
            result = await self.world.submit_intent(observation.tick_id, intent)
        except grpc.RpcError as error:
            logger.warning("submit_intent_rpc_error", details=error.details())
            result_text = f"rpc_error: {error.details()}"
        else:
            result_text = (
                "accepted" if result.accepted else f"rejected: {result.reason}"
            )
            if not result.accepted:
                logger.warning(
                    "intent_rejected", tick=observation.tick_id, reason=result.reason
                )

        if sink is not None:
            sink(result_text)

        await self._report_status()

    async def _choose_intent(
        self, digest: TickDigest
    ) -> tuple[pb.Intent, ResultSink | None]:
        """Pick this tick's intent and the callback that records its outcome."""
        reflex_stint = self._reflex_stint
        if reflex_stint is not None:
            self._refuse_direct_requests(INTERRUPTED_BY_REFLEX)
            intent = await reflex_stint.decide(digest)
            if reflex_stint.finished:
                self._finish_reflex()
                return intent, None
            return intent, reflex_stint.record_intent_result

        session = self._conversation
        if session is not None:
            # A queued stint simply waits: the session owns the body until the
            # seat is gone, and `_stint_or_planning_intent` is never reached.
            self._refuse_direct_requests(
                conversation_interruption(session.conversation_id)
            )
            intent = session.decide(digest)
            if session.finished:
                self._finish_conversation()
                return intent, None
            return intent, None

        return await self._stint_or_planning_intent(digest)

    async def _stint_or_planning_intent(
        self, digest: TickDigest
    ) -> tuple[pb.Intent, ResultSink | None]:
        stint = self._active_stint
        if stint is not None and not stint.finished:
            intent = await stint.decide(digest)
            if stint.finished:
                self._finish_stint()
                return intent, None
            return intent, stint.record_intent_result

        if stint is not None and stint.finished:
            self._finish_stint()

        await self._await_planner_work()

        if self._active_stint is None and not self._stint_requests.empty():
            stint = self._begin_stint(self._stint_requests.get_nowait())
            intent = await stint.decide(digest)
            if stint.finished:
                self._finish_stint()
                return intent, None
            return intent, stint.record_intent_result

        return self._planning_intent()

    def _submit_window_ms(self) -> float:
        """Milliseconds left before this tick's intent deadline, minus the margin.

        Zero when the world sent no deadline (fakes and tests) or when the
        clock says the window is gone. Capped at MAX_PLANNER_WAIT_MS so a
        clock skew between world and agent cannot stall the tick loop.
        """
        if self._deadline_ms <= 0.0:
            return 0.0
        left = self._deadline_ms - time.time() * 1000.0 - SUBMIT_MARGIN_MS
        return min(MAX_PLANNER_WAIT_MS, max(0.0, left))

    async def _await_planner_work(self) -> None:
        """Let the planner use the rest of this tick's window to queue an action.

        The future behind a single-tick tool is resolved at the top of this
        tick, but the planner task is only *scheduled* there: without this
        wait the tick loop went on to choose and submit this tick's intent
        without yielding, so the planner's next action could not go out until
        the tick after, and every single-tick tool cost two ticks (one to act,
        one to wait). An action that comes back inside the world's own
        deadline now goes out on this tick.
        """
        if self._awaiting_direct is not None or self._pending_thought:
            return
        if not self._body_is_active() or self.mode != MODE_PLANNING:
            return
        while self._direct_requests.empty() and self._stint_requests.empty():
            if self._submit_window_ms() <= 0.0:
                return
            await asyncio.sleep(PLANNER_POLL_SECONDS)

    def _planning_intent(self) -> tuple[pb.Intent, ResultSink | None]:
        if self._awaiting_direct is None and not self._direct_requests.empty():
            request = self._direct_requests.get_nowait()
            self._awaiting_direct = request
            return request.intent, None

        awaiting = self._awaiting_direct
        if awaiting is not None and awaiting.remaining_ticks > 1:
            awaiting.remaining_ticks -= 1
            return awaiting.intent, None

        if self._pending_thought:
            thought = self._pending_thought
            self._pending_thought = ""
            return (
                pb.Intent(
                    say=pb.SayIntent(text=thought[:400], channel=THOUGHT_CHANNEL)
                ),
                None,
            )

        return pb.Intent(wait=pb.WaitIntent()), None

    # -- life and the journal -----------------------------------------------

    def _note_life_transitions(self, digest: TickDigest) -> None:
        """Death and respawn: end the turn, and write the journal on death.

        Dying is the other end of a day (docs/12_sleep_journal.md): what the
        turn was doing is void, the planner is told so through the ordinary
        note queue, and its budget is spent so it writes its reflection and
        stops.
        """
        for entity_id in digest.deaths:
            if entity_id == self.entity_id:
                continue
            death = self._model.death_of(entity_id)
            if death is not None:
                self.planner.day_log.add(self._model.tick, KIND_EVENT, death.fact())
        if digest.self_died:
            tick = self._model.tick
            self.planner.day_log.add(tick, KIND_EVENT, f"you died at tick {tick}")
            self._note_for_planner(DEATH_NOTE.format(tick=tick))
            self.planner.end_turn_now()
            self._start_journal_rewrite(TRIGGER_DEATH)
        if digest.self_respawned:
            self.planner.day_log.add(self._model.tick, KIND_EVENT, "you respawned")
            self.planner.note_life_event(LIFE_RESPAWNED)

    def _start_journal_rewrite(self, trigger: str) -> None:
        """Start the day's journal rewrite in the background, if none is running.

        A second trigger while one is in flight is dropped: the day log it
        would have been given is already inside the running rewrite.
        """
        task = self._journal_task
        if task is not None and not task.done():
            logger.debug(
                "journal_rewrite_skipped", entity_id=self.entity_id, trigger=trigger
            )
            return
        entries = self.planner.day_log.take()
        self._journal_task = asyncio.create_task(
            self._rewrite_journal(trigger, entries)
        )
        self._background.add(self._journal_task)
        self._journal_task.add_done_callback(self._background.discard)

    async def _rewrite_journal(
        self, trigger: str, entries: Sequence[DayLogEntry]
    ) -> None:
        """Ask the writer for a new journal and save it; never fail the agent."""
        started = time.monotonic()
        path = self.trace.memory_path
        try:
            journal = Journal.load(path, self.entity_id)
            rewrite = await self.journal_writer.rewrite(
                journal,
                render_day_log(entries),
                self.entity_id,
                self._model.clock.moon_text(),
            )
            rewrite.journal.save(path)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - a lost journal is not fatal
            logger.warning(
                "journal_rewrite_failed",
                entity_id=self.entity_id,
                trigger=trigger,
                error=str(error),
            )
            # The day is not thrown away with the call: the next rewrite gets
            # these entries again, in front of whatever has happened since.
            self.planner.day_log.restore(entries)
            self.trace.planner.write(
                {
                    "event": "journal_rewrite_failed",
                    "entity_id": self.entity_id,
                    "tick": self._model.tick,
                    "trigger": trigger,
                    "error": str(error),
                    "duration_ms": rewrite_duration_ms(started),
                }
            )
            return
        self.ledger.add_journal(rewrite.usage)
        logger.info(
            "journal_rewritten",
            entity_id=self.entity_id,
            trigger=trigger,
            truncated=list(rewrite.truncated),
        )
        self.trace.planner.write(
            {
                "event": "journal_rewrite",
                "entity_id": self.entity_id,
                "tick": self._model.tick,
                "trigger": trigger,
                "sections": dict(rewrite.token_counts),
                "journal": rewrite.journal.all_sections(),
                "truncated": list(rewrite.truncated),
                "duration_ms": rewrite_duration_ms(started),
                "usage": dict(rewrite.usage),
                "model": rewrite.model,
            }
        )

    # -- sleep --------------------------------------------------------------

    async def _sleeping_tick(
        self, observation: pb.Observation, digest: TickDigest
    ) -> None:
        """A tick spent asleep: nothing thinks, and only a `wake` is submitted.

        Jev, the planner and the converser are all skipped, so whatever mode
        the actor was in simply resumes on the tick it wakes.
        """
        self._resolve_awaiting_direct(digest)
        request = self._wake_request_while_asleep()
        if request is not None:
            self._awaiting_direct = request
            try:
                await self.world.submit_intent(observation.tick_id, request.intent)
            except grpc.RpcError as error:
                logger.warning("submit_intent_rpc_error", details=error.details())
        await self._report_status()

    def _wake_request_while_asleep(self) -> _DirectRequest | None:
        """The queued `wake` the planner asked for, if that is what is next.

        The world refuses every other intent from a sleeper, so any other
        queued action is answered with that refusal instead of being held.
        """
        if self._awaiting_direct is not None:
            return None
        while not self._direct_requests.empty():
            request = self._direct_requests.get_nowait()
            if request.intent.WhichOneof("action") == WAKE_ACTION:
                return request
            if not request.future.done():
                request.future.set_result(
                    ActionOutcome.never_ran(request.description, ASLEEP_REJECTION)
                )
        return None

    def _note_sleep_transitions(self, digest: TickDigest) -> None:
        """Record falling asleep and waking in the stints trace."""
        info = self._model.self_info
        if info.asleep and self._asleep_since < 0:
            self._asleep_since = self._model.tick
            self._sleep_fatigue_before = info.fatigue
            self._sleep_place = sleep_place(digest)
            self.trace.stints.write(
                {
                    "event": "sleep_start",
                    "entity_id": self.entity_id,
                    "tick": self._model.tick,
                    "where": self._sleep_place,
                    "fatigue": info.fatigue,
                }
            )
            logger.info("fell_asleep", where=self._sleep_place, fatigue=info.fatigue)
            self.planner.day_log.add(
                self._model.tick,
                KIND_EVENT,
                f"you lay down to sleep on {self._sleep_place}",
            )
            # A collapse, or a sleep Jev chose, ends the turn just as the
            # `sleep` tool does; that tool says so itself, so it gets no note.
            if not self._wake_waiters:
                self._note_for_planner(
                    SLEEP_NOTE.format(tick=self._model.tick, where=self._sleep_place)
                )
            # Everything the body was doing is over before the turn is ended
            # and the journal is written, so the closing note of a conversation
            # reaches the same day's rewrite where it can.
            self._drain_for_sleep(digest)
            self.planner.end_turn_now()
            self._start_journal_rewrite(TRIGGER_SLEEP)
            return
        if info.asleep or self._asleep_since < 0:
            return
        self._drained_this_sleep = False
        record = SleepRecord(
            start_tick=self._asleep_since,
            end_tick=self._model.tick,
            reason=wake_reason(digest),
            fatigue_before=self._sleep_fatigue_before,
            fatigue_after=info.fatigue,
            where=self._sleep_place,
            food_after=info.food,
        )
        self._asleep_since = -1
        self._last_sleep = record
        self.trace.stints.write(
            {
                "event": "sleep_end",
                "entity_id": self.entity_id,
                "tick": record.end_tick,
                "where": record.where,
                "reason": record.reason,
                "ticks_slept": record.ticks_slept,
                "fatigue_before": record.fatigue_before,
                "fatigue_after": record.fatigue_after,
            }
        )
        logger.info("woke_up", reason=record.reason, ticks=record.ticks_slept)
        self.planner.day_log.add(self._model.tick, KIND_EVENT, record.to_text())
        self.planner.note_life_event(LIFE_WOKE)
        self._wake_waiters.release(record.to_text())

    def _drain_for_sleep(self, digest: TickDigest) -> None:
        """Finish everything holding the body, because it has fallen asleep.

        A sleeper submits nothing, so a stint, a reflex stint or a conversation
        seat that outlived the sleep would hold the planner's tool call - and
        with it the turn `end_turn_now` has just ended - for the whole night.
        Each one is therefore finished here and reported as usual. The world
        chooses this sleep on a new-moon night (docs/14 section 1) and the
        settler chooses it otherwise; the end reason says which.
        """
        if self._drained_this_sleep:
            return
        self._drained_this_sleep = True
        reason = END_NEW_MOON if self._model.clock.new_moon_tonight else END_ASLEEP
        # The action that put the body to sleep is this tick's own action, so
        # it is answered before the rest of the queue is refused.
        self._resolve_awaiting_direct(digest)
        self._refuse_direct_requests(ASLEEP_REJECTION)
        reflex_stint = self._reflex_stint
        if reflex_stint is not None:
            reflex_stint.finish(reason)
            self._finish_reflex()
        stint = self._active_stint
        if stint is not None:
            stint.finish(reason)
            self._finish_stint()
        self._refuse_queued_stints(reason)
        session = self._conversation
        if session is not None:
            session.finish(sleep_end_reason(self._model))
            self._finish_conversation()
        else:
            # With a seat, the closing report answers the waiting tool. Without
            # one the conversation can no longer start, and the grace period
            # (4 ticks) outlasts the save tick (3 ticks into the night).
            self._expire_conversation_waiters(now=True)

    def _refuse_queued_stints(self, reason: str) -> None:
        """Answer every stint still waiting its turn as one that never ran.

        A stint the planner asks for while a reflex stint holds the body waits
        in the queue for the reflex to end. When the body falls asleep instead,
        nothing will ever start it: it would sit there all night and make
        `drained()` answer "a stint is queued", which abandons the save.
        """
        for request in _drain(self._stint_requests):
            if not request.future.done():
                request.future.set_result(self._refused_report(request.brief, reason))

    # -- reflex -------------------------------------------------------------

    def _maybe_start_reflex(self, digest: TickDigest) -> None:
        """Start the reflex stint when the brief's trigger has fired.

        An ordinary Jev stint is never pre-empted: Jev sees the threat block
        itself and is already acting under a brief the planner wrote.
        """
        if self._reflex_stint is not None:
            return
        stint = self._active_stint
        if stint is not None and stint.driver is None:
            return
        trigger = self._reflex_watch.trigger(self._model, digest)
        if not trigger:
            return

        interrupted = INTERRUPTED_PLANNING
        if stint is not None:
            interrupted = INTERRUPTED_DRIVER_STINT
            stint.finish(END_PREEMPTED_BY_REFLEX)
            self._finish_stint(hold=True)
        elif self._conversation is not None:
            interrupted = INTERRUPTED_CONVERSATION
        else:
            self._refuse_direct_requests(INTERRUPTED_BY_REFLEX)

        self._reflex_watch.begin()
        self._reflex_start_tick = self._model.tick
        self._reflex_start_health = self._model.self_info.health
        self._reflex_stint = Stint(
            self._reflex_watch.brief.to_brief(),
            self._model,
            self.jev,
            trace=self.trace,
            end_check=self._reflex_watch.end_reason,
            kind=STINT_KIND_REFLEX,
            start_fields={"trigger": trigger, "interrupted": interrupted},
        )
        self.mode = MODE_REFLEX
        logger.info("reflex_started", trigger=trigger, interrupted=interrupted)

    def _finish_reflex(self) -> None:
        """Publish the reflex line and hand the body back to whatever was paused."""
        stint = self._reflex_stint
        self._reflex_stint = None
        if stint is None:
            return
        self._reflex_watch.note_end(self._model.tick)
        line = reflex_report_line(
            self._reflex_start_tick,
            self._model.tick,
            stint.end_reason,
            self._reflex_start_health,
            self._model.self_info.health,
        )
        self._note_for_planner(line)
        self._release_held_stint(line)
        # The conversation session was never dropped, so it simply resumes; if
        # the world took the seat away it will notice on its next tick.
        self.mode = (
            MODE_CONVERSATION if self._conversation is not None else MODE_PLANNING
        )

    def _refuse_direct_requests(self, message: str) -> None:
        """Answer every in-flight and queued single-tick action with `message`.

        Used by both interruptions: a reflex stint and a conversation that
        started while the planner was mid-turn.
        """
        awaiting = self._awaiting_direct
        if awaiting is not None:
            self._awaiting_direct = None
            if not awaiting.future.done():
                awaiting.future.set_result(
                    ActionOutcome.never_ran(awaiting.description, message)
                )
        for request in _drain(self._direct_requests):
            if not request.future.done():
                request.future.set_result(
                    ActionOutcome.never_ran(request.description, message)
                )

    # -- conversations ------------------------------------------------------

    def _detect_join(self, digest: TickDigest) -> None:
        """Enter conversation mode when the world says the actor took a seat.

        The seat may come from an `open`, a `join`, a `hail` of another
        settler, or someone hailing this actor. In the last case the actor
        asked for nothing, so whatever single-tick action it had in flight is
        answered as interrupted.
        """
        seat = joined_conversation(digest)
        conversation_id = seat.conversation_id
        if not conversation_id or self._conversation is not None:
            return
        via = seat.action
        stint = self._active_stint
        purpose = self._conversation_purpose(seat, stint)
        if stint is not None:
            stint.finish(END_JOINED_CONVERSATION)
            self._finish_stint(hold=True)
        elif via in UNASKED_VIA:
            # Nothing in flight belongs to this seat: the planner asked for an
            # action and someone hailed this actor instead.
            self._refuse_direct_requests(conversation_interruption(conversation_id))
        self._begin_conversation(conversation_id, via, purpose)
        self._pending_purpose = ""

    def _conversation_purpose(self, seat: Seat, stint: Stint | None) -> str:
        """Why this actor started the conversation, or `""` (docs/09 item 4).

        A settler that was hailed or that only joined has no purpose. A
        Jev-driven `hail:<settler>` option is matched back to the brief's own
        `BriefHail`; a planner `open_conversation`/`talk_to` reads the value
        `set_conversation_purpose` stashed just before the intent.
        """
        if stint is not None:
            if seat.action != ACTION_HAIL:
                return ""
            for hail in stint.brief.hails:
                if hail.settler == seat.target:
                    return hail.purpose
            return ""
        if seat.action in (ACTION_OPEN, ACTION_HAIL):
            return self._pending_purpose
        return ""

    def _begin_conversation(
        self, conversation_id: str, via: str = ACTION_JOIN, purpose: str = ""
    ) -> None:
        session = ConversationSession(
            conversation_id,
            self._model,
            self.converser,
            trace=self.trace,
            memory_path=self.planner.memory_path,
            reflex_line=lambda: self.reflex.prompt_line(),
            alert_line=self._alert_line,
            purpose=purpose,
        )
        session.begin(via)
        self._conversation = session
        self.mode = MODE_CONVERSATION
        logger.info("conversation_started", conversation_id=conversation_id, via=via)

    def _alert_line(self) -> str:
        """The threat alert as the planner would see it, or `""`."""
        model = self._model
        return threat_alert(model, alert_window_start(model.tick, 0))

    def _finish_conversation(self) -> None:
        """Hand the body back and make the after-conversation note call."""
        session = self._conversation
        self._conversation = None
        self.mode = MODE_PLANNING
        if session is None:
            return
        logger.info(
            "conversation_ended",
            conversation_id=session.conversation_id,
            reason=session.end_reason,
        )
        self._closing_conversations += 1
        self._spawn(self._report_conversation(session))

    async def _report_conversation(self, session: ConversationSession) -> None:
        """Write the note, then deliver the report to whoever is owed it.

        A tool parked on `await_conversation` gets it, or a stint the join cut
        short carries it. A conversation nobody asked for - one another settler
        started by hailing this actor - has no such owner, so the report is
        queued as a note like a reflex line.
        """
        try:
            report = await session.write_report()
        finally:
            # Counted down whatever happened, so a failed closing call cannot
            # leave the settler looking busy forever (docs/14 section 2).
            self._closing_conversations -= 1
        text = report.to_text()
        delivered = self._held_stint is not None
        self._release_held_stint(text)
        for waiter in list(self._conversation_waiters):
            self._conversation_waiters.remove(waiter)
            delivered = True
            if not waiter.future.done():
                waiter.future.set_result(report)
        if not delivered:
            self._note_for_planner(text)

    def _expire_conversation_waiters(self, *, now: bool = False) -> None:
        """Release a planner tool whose conversation never started.

        `now` releases every waiter whatever its deadline, for a body that has
        fallen asleep and can no longer take a seat.
        """
        if self._conversation is not None:
            return
        for waiter in list(self._conversation_waiters):
            if not now and self._model.tick < waiter.deadline_tick:
                continue
            self._conversation_waiters.remove(waiter)
            if not waiter.future.done():
                waiter.future.set_result(None)

    def _spawn(self, coroutine: Coroutine[Any, Any, None]) -> None:
        """Run a coroutine outside the tick loop and keep a reference to it."""
        task = asyncio.create_task(coroutine)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    # -- stint / direct-action bookkeeping ----------------------------------

    def _begin_stint(self, request: _StintRequest) -> Stint:
        self._active_request = request
        stint = Stint(
            request.brief,
            self._model,
            self.jev,
            trace=self.trace,
            driver=request.driver,
            end_check=request.end_check,
        )
        self._active_stint = stint
        self.mode = MODE_STINT
        logger.info("stint_started", brief=request.brief.instruction)
        return stint

    def _finish_stint(self, *, hold: bool = False) -> None:
        """Report the finished stint, or hold it back for what interrupted it.

        A held report is released by `_release_held_stint` once the reflex or
        the conversation that pre-empted the stint has produced its own text,
        so the planner's one tool call returns all of it together.
        """
        stint = self._active_stint
        request = self._active_request
        self._active_stint = None
        self._active_request = None
        self.mode = MODE_PLANNING
        if stint is None:
            return
        report = stint.build_report()
        if hold and request is not None:
            self._held_stint = _HeldStint(request=request, report=report)
            return
        self.planner.note_report(report)
        if request is not None and not request.future.done():
            request.future.set_result(report)

    def _release_held_stint(self, appended: str) -> None:
        """Resolve a held stint report with the interrupting report appended."""
        held = self._held_stint
        if held is None:
            return
        self._held_stint = None
        held.report.append(appended)
        self.planner.note_report(held.report)
        if not held.request.future.done():
            held.request.future.set_result(held.report)

    def _resolve_awaiting_direct(self, digest: TickDigest) -> None:
        """Turn the world's own-action events into the planner's tool result."""
        request = self._awaiting_direct
        if request is None:
            return
        if request.remaining_ticks > 1:
            return
        outcome = ActionOutcome.submitted_only(request.description)
        for acted in digest.own_actions:
            outcome = ActionOutcome.from_event(
                request.description, acted.action_type, acted.success, acted.details
            )
            break
        self._awaiting_direct = None
        if not request.future.done():
            request.future.set_result(outcome)

    def _fail_pending(self, reason: str) -> None:
        for request in _drain(self._direct_requests):
            if not request.future.done():
                request.future.set_result(
                    ActionOutcome.never_ran(request.description, reason)
                )
        awaiting = self._awaiting_direct
        if awaiting is not None and not awaiting.future.done():
            awaiting.future.set_result(
                ActionOutcome.never_ran(awaiting.description, reason)
            )
        self._awaiting_direct = None
        for waiter in self._conversation_waiters:
            if not waiter.future.done():
                waiter.future.set_result(None)
        self._conversation_waiters.clear()
        self._wake_waiters.release("")
        for stint_request in _drain(self._stint_requests):
            if not stint_request.future.done():
                stint_request.future.set_exception(RuntimeError(reason))
        held = self._held_stint
        self._held_stint = None
        if held is not None and not held.request.future.done():
            held.request.future.set_result(held.report)

    # -- saving and resuming (docs/14) --------------------------------------

    def drained(self) -> DrainState:
        """Whether a snapshot of this settler would be a whole truth.

        Drained means the body is asleep or dead, nothing holds it (no stint,
        reflex stint or conversation), no model call is in flight behind the
        agent's back, the planner has parked itself on `await_active`, and no
        queue holds a request.

        A `sleep` tool parked on `await_wake` counts as a parked planner: it
        is what a settler that chose to sleep looks like all night (its turn
        does not end until it wakes), and the snapshot carries no futures, so a
        resumed settler simply takes a fresh turn when it wakes.
        """
        info = self._model.self_info
        if info.alive and not info.asleep:
            return DrainState.busy("the body is awake")
        if self._reflex_stint is not None:
            return DrainState.busy("a reflex stint is running")
        if self._active_stint is not None:
            return DrainState.busy("a stint is running")
        if self._held_stint is not None:
            return DrainState.busy("a stint report is held back")
        if not self._stint_requests.empty():
            return DrainState.busy("a stint is queued")
        if self._conversation is not None:
            return DrainState.busy("a conversation seat is held")
        if self._closing_conversations:
            return DrainState.busy("a conversation closing note is in flight")
        journal = self._journal_task
        if journal is not None and not journal.done():
            return DrainState.busy("the journal rewrite is in flight")
        if self._awaiting_direct is not None or not self._direct_requests.empty():
            return DrainState.busy("a single-tick action is in flight")
        if self._conversation_waiters:
            return DrainState.busy("a tool is waiting for a conversation")
        if not self._active_waiters and not self._wake_waiters:
            return DrainState.busy("the planner turn has not ended yet")
        return DRAINED

    def sleep_snapshot(self) -> SleepSnapshot:
        """The tick loop's sleep bookkeeping, for a snapshot."""
        last = self._last_sleep
        return SleepSnapshot(
            asleep_since=self._asleep_since,
            fatigue_before=self._sleep_fatigue_before,
            where=self._sleep_place,
            last_sleep_recorded=last is not None,
            last_start_tick=0 if last is None else last.start_tick,
            last_end_tick=0 if last is None else last.end_tick,
            last_reason="" if last is None else last.reason,
            last_fatigue_before=0 if last is None else last.fatigue_before,
            last_fatigue_after=0 if last is None else last.fatigue_after,
            last_where=GROUND_SLEEP_PLACE if last is None else last.where,
            last_food_after=0 if last is None else last.food_after,
        )

    def load_snapshot(self, snapshot: AgentSnapshot) -> None:
        """Put a snapshot's state into this agent, before it connects.

        Called once, on a process started with `--resume-from`; the world then
        re-delivers the snapshot's own tick, which `WorldModel.update` folds as
        a refresh rather than as a second tick.
        """
        self._model = WorldModel.from_payload(snapshot.world_model)
        self.planner.load_payload(snapshot.planner)
        self.ledger.load_payload(snapshot.cost_ledger)
        self._reflex_watch.load_payload(snapshot.reflex_watch)
        sleep = snapshot.sleep
        self._asleep_since = sleep.asleep_since
        self._sleep_fatigue_before = sleep.fatigue_before
        self._sleep_place = sleep.where
        self._last_sleep = (
            SleepRecord(
                start_tick=sleep.last_start_tick,
                end_tick=sleep.last_end_tick,
                reason=sleep.last_reason,
                fatigue_before=sleep.last_fatigue_before,
                fatigue_after=sleep.last_fatigue_after,
                where=sleep.last_where,
                food_after=sleep.last_food_after,
            )
            if sleep.last_sleep_recorded
            else None
        )
        self._notes_for_tools = list(snapshot.notes_for_tools)
        self._notes_for_prompt = list(snapshot.notes_for_prompt)
        # The sleep it was saved in has already been drained through; doing it
        # again would refuse requests nobody made and end nothing.
        self._drained_this_sleep = self._asleep_since >= 0
        logger.info(
            "snapshot_restored",
            entity_id=self.entity_id,
            tick=snapshot.tick,
            turn=self.planner.turn,
        )

    def save_directory(self, tick: int) -> Path:
        """`<run dir>/saves/tick-<T>`, the directory the world made for the save."""
        run_dir = os.environ.get(RUN_DIR_ENV, "")
        root = Path(run_dir) if run_dir else self.log_root.parent
        return root / SAVES_DIR_NAME / f"tick-{tick}"

    async def _maybe_save(self, tick: int) -> None:
        """Write this settler's snapshot for the save the world is waiting on.

        The world has paused and is waiting for one file per settler, so
        blocking the tick loop here costs nothing; the lease renewal is its own
        task and keeps running. A settler that has not drained inside the wait
        writes nothing at all rather than a partial truth, and says why.
        """
        deadline = time.monotonic() + save_wait_seconds()
        state = self.drained()
        while not state.drained and time.monotonic() < deadline:
            await asyncio.sleep(SAVE_POLL_SECONDS)
            state = self.drained()
        if not state.drained:
            logger.error(
                "save_skipped_not_drained",
                entity_id=self.entity_id,
                tick=tick,
                reason=state.reason,
            )
            self.trace.planner.write(
                {
                    "event": "save_skipped",
                    "entity_id": self.entity_id,
                    "tick": tick,
                    "reason": state.reason,
                }
            )
            return
        path = snapshot_path(self.save_directory(tick), self.entity_id)
        try:
            write_snapshot(path, capture(self))
        except OSError as error:
            logger.error(
                "save_write_failed",
                entity_id=self.entity_id,
                tick=tick,
                path=str(path),
                error=str(error),
            )
            self.trace.planner.write(
                {
                    "event": "save_failed",
                    "entity_id": self.entity_id,
                    "tick": tick,
                    "path": str(path),
                    "error": str(error),
                }
            )
            return
        logger.info("save_written", entity_id=self.entity_id, tick=tick, path=str(path))
        self.trace.planner.write(
            {
                "event": "save_written",
                "entity_id": self.entity_id,
                "tick": tick,
                "path": str(path),
                "bytes": path.stat().st_size,
            }
        )

    # -- status -------------------------------------------------------------

    async def _report_status(self) -> None:
        session = self._conversation
        if session is not None:
            status = StatusLine(
                mode=self.mode,
                brief="in conversation",
                thought=self.planner.last_thought,
                detail_json=session.status_json(),
                cost_json=self.ledger.as_json(),
            )
        else:
            stint = self._reflex_stint or self._active_stint
            status = StatusLine(
                mode=self.mode,
                brief=stint.brief.summary() if stint is not None else "",
                thought=self.planner.last_thought,
                detail_json=stint.status_json() if stint is not None else "",
                cost_json=self.ledger.as_json(),
            )
        if status == self._last_status:
            return
        self._last_status = status
        try:
            await self.world.report_status(*status.as_arguments())
        except grpc.RpcError as error:
            logger.debug("status_report_failed", details=error.details())


async def run_agent(
    server_address: str,
    entity_id: str,
    *,
    log_root: Path = USE_RUN_DIR,
    planner_model: str = "",
    jev_model: str = "",
    journal_model: str = "",
    settler_count: int = DEFAULT_SETTLER_COUNT,
    resume_from: Path | None = None,
) -> None:
    """Build every piece and run one actor until it is interrupted.

    `resume_from` is a save directory (`runs/<run>/saves/tick-<T>`): the agent
    is built as usual, restored from that save, and only then connected, so
    the re-delivered observation for tick `T` lands on a model that has
    already seen it (docs/14 section 4).
    """
    world = WorldClient(server_address, entity_id)
    jev = TypeSafeJevClient(jev_model or os.environ.get("JEV_MODEL", "jev-latest"))
    agent = JevAgent(
        world,
        jev,
        entity_id,
        log_root=log_root,
        planner_model=planner_model,
        journal_model=journal_model,
        settler_count=settler_count,
    )
    if resume_from is not None:
        restore(agent, read_snapshot(snapshot_path(resume_from, entity_id), entity_id))
    try:
        await agent.run()
    finally:
        await jev.aclose()
        await world.close()
        agent.trace.close()
