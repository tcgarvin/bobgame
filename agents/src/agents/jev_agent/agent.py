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
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Coroutine, TypeVar

import grpc
import structlog

from .. import world_pb2 as pb
from .client import WorldClient
from .conversation import (
    ConversationReport,
    ConversationSession,
    Converser,
    ModelConverser,
    joined_conversation_id,
)
from .jevclient import JevClient, TypeSafeJevClient
from .planner import Planner, alert_window_start, threat_alert
from .reflex import (
    EMPTY_REFLEX,
    INTERRUPTED_CONVERSATION,
    INTERRUPTED_DRIVER_STINT,
    INTERRUPTED_PLANNING,
    ReflexBrief,
    ReflexStore,
    ReflexWatch,
    reflex_report_line,
)
from .stint import (
    END_JOINED_CONVERSATION,
    END_PREEMPTED_BY_REFLEX,
    STINT_KIND_REFLEX,
    Brief,
    Stint,
    StintDriver,
    StintReport,
)
from .pricing import pricing_payload
from .tracelog import AgentTrace, resolve_log_root
from .worldmodel import TickDigest, WorldModel

logger = structlog.get_logger(__name__)

MODE_PLANNING = "planning"
MODE_STINT = "stint"
MODE_REFLEX = "reflex"
MODE_CONVERSATION = "conversation"
MODE_IDLE = "idle"

# Called with "accepted" or a rejection string once the world has answered.
ResultSink = Callable[[str], None]

_T = TypeVar("_T")

THOUGHT_CHANNEL = "thought"

INTERRUPTED_BY_REFLEX = "interrupted: reflex stint started"

# How long a planner tool that has just opened or joined a conversation waits
# for the tick loop to see the object before it gives up on it.
CONVERSATION_START_GRACE_TICKS = 4

# How long the `sleep` tool waits for the tick loop to see the actor asleep
# before it decides the sleep never happened.
SLEEP_START_GRACE_TICKS = 3

# The world's own wording for falling asleep, collapsing and waking.
SLEEP_ACTION = "sleep"
COLLAPSE_ACTION = "collapse"
WAKE_ACTION = "wake"
GROUND_SLEEP_PLACE = "the ground"
UNKNOWN_WAKE_REASON = "unknown"

ASLEEP_REJECTION = "failed: asleep"


@dataclass(frozen=True)
class SleepRecord:
    """One completed sleep, as the planner's `sleep` tool reports it."""

    start_tick: int
    end_tick: int
    reason: str
    fatigue_before: int
    fatigue_after: int
    where: str

    @property
    def ticks_slept(self) -> int:
        """World ticks between falling asleep and waking."""
        return max(0, self.end_tick - self.start_tick)

    def to_text(self) -> str:
        """The line the `sleep` tool returns."""
        return (
            f"slept on {self.where} from tick {self.start_tick} to "
            f"{self.end_tick} ({self.ticks_slept} ticks); woke because "
            f"{self.reason}; fatigue {self.fatigue_before} -> {self.fatigue_after}"
        )


def sleep_place(digest: TickDigest) -> str:
    """Where the world says the actor fell asleep, from its own action event."""
    for acted in digest.own_actions:
        if acted.action_type in (SLEEP_ACTION, COLLAPSE_ACTION) and acted.success:
            return acted.details or GROUND_SLEEP_PLACE
    return GROUND_SLEEP_PLACE


def wake_reason(digest: TickDigest) -> str:
    """Why the actor woke, from the world's `wake` action event."""
    for acted in digest.own_actions:
        if acted.action_type == WAKE_ACTION and acted.success:
            return acted.details or UNKNOWN_WAKE_REASON
    return UNKNOWN_WAKE_REASON


@dataclass
class _StintRequest:
    """A planner `start_stint` waiting for the tick loop to pick it up."""

    brief: Brief
    future: asyncio.Future[StintReport]
    driver: StintDriver | None = None


@dataclass
class _DirectRequest:
    """A planner single-tick action waiting to be submitted and resolved."""

    intent: pb.Intent
    description: str
    future: asyncio.Future[str]
    remaining_ticks: int = 1
    results: list[str] = field(default_factory=list)


@dataclass
class _ConversationWaiter:
    """A planner tool parked until the conversation it started has ended."""

    future: asyncio.Future[ConversationReport | None]
    deadline_tick: int


@dataclass
class _WakeWaiter:
    """A planner `sleep` tool parked until the actor is awake again."""

    # The rendered sleep, or "" when no sleep was seen.
    future: asyncio.Future[str]
    deadline_tick: int


@dataclass
class _HeldStint:
    """A stint report kept back until the thing that interrupted it is over."""

    request: _StintRequest
    report: StintReport


def _write_pricing(trace: AgentTrace, planner_model: str) -> None:
    """Record the prices this run is billed at, next to the other traces.

    Written once at startup so a later analysis of the run uses the price in
    force then rather than today's constant (docs/11_cost_accounting.md). A
    disabled trace writes no files at all, and a file that cannot be written
    complains rather than taking the actor down.
    """
    if not trace.enabled:
        return
    path = trace.pricing_path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(pricing_payload(planner_model), indent=2), encoding="utf-8"
        )
    except OSError as error:
        logger.warning("pricing_write_failed", path=str(path), error=str(error))


class JevAgent:
    """A planner and a Jev executor sharing one entity and one tick loop."""

    def __init__(
        self,
        world: WorldClient,
        jev: JevClient,
        entity_id: str,
        *,
        log_root: Path | None = None,
        planner_model: str = "",
        converser: Converser | None = None,
    ) -> None:
        self.world = world
        self.jev = jev
        self.entity_id = entity_id
        self.log_root = resolve_log_root() if log_root is None else log_root
        self.trace = AgentTrace(entity_id, self.log_root)
        self._model = WorldModel(entity_id)

        self.mode = MODE_IDLE
        self.planner = Planner(
            self, entity_id, model_name=planner_model, trace=self.trace
        )
        self.converser: Converser = (
            ModelConverser(planner_model) if converser is None else converser
        )

        _write_pricing(self.trace, self.planner.model_name)

        self.reflex_store = ReflexStore(self.trace.reflex_path)
        self._reflex_watch = ReflexWatch(self.reflex_store.load())
        self._reflex_stint: Stint | None = None
        self._reflex_start_tick = 0
        self._reflex_start_health = 0
        self._reflex_notes_for_tools: list[str] = []
        self._reflex_notes_for_prompt: list[str] = []

        self._stint_requests: asyncio.Queue[_StintRequest] = asyncio.Queue()
        self._direct_requests: asyncio.Queue[_DirectRequest] = asyncio.Queue()
        self._active_stint: Stint | None = None
        self._active_request: _StintRequest | None = None
        self._awaiting_direct: _DirectRequest | None = None
        self._held_stint: _HeldStint | None = None
        self._conversation: ConversationSession | None = None
        self._conversation_waiters: list[_ConversationWaiter] = []
        # -1 means awake; otherwise the tick the current sleep began.
        self._asleep_since = -1
        self._sleep_fatigue_before = 0
        self._sleep_place = GROUND_SLEEP_PLACE
        self._last_sleep: SleepRecord | None = None
        self._wake_waiters: list[_WakeWaiter] = []
        self._background: set[asyncio.Task[None]] = set()
        self._pending_thought = ""
        self._last_status = ("", "", "", "")
        self._running = False

    # -- AgentBridge --------------------------------------------------------

    @property
    def model(self) -> WorldModel:
        """The shared world model, updated at the top of every tick."""
        return self._model

    @property
    def reflex(self) -> ReflexBrief:
        """The brief code runs when a wolf is close or the actor is bitten."""
        return self._reflex_watch.brief

    def set_reflex(self, brief: ReflexBrief) -> None:
        """Register (or replace) the reflex brief and persist it."""
        self._reflex_watch.set_brief(brief)
        self.reflex_store.save(brief)
        logger.info("reflex_registered", instruction=brief.instruction)

    def clear_reflex(self) -> None:
        """Forget the reflex brief; nothing fires until a new one is set."""
        self._reflex_watch.set_brief(EMPTY_REFLEX)
        self.reflex_store.save(EMPTY_REFLEX)

    def drain_reflex_notes(self, *, for_prompt: bool = False) -> list[str]:
        """Reflex report lines not yet shown, emptied as they are taken.

        There are two queues over one source, because the contract shows each
        line both in the next tool result and in the next turn prompt.
        """
        queue = (
            self._reflex_notes_for_prompt
            if for_prompt
            else (self._reflex_notes_for_tools)
        )
        notes = list(queue)
        queue.clear()
        return notes

    async def run_stint(
        self, brief: Brief, driver: StintDriver | None = None
    ) -> StintReport:
        """Queue a stint and wait for the tick loop to finish running it.

        With a `driver`, code chooses the action every tick and Jev is not
        called at all; that is how the planner's `build` tool works.
        """
        future: asyncio.Future[StintReport] = asyncio.get_running_loop().create_future()
        await self._stint_requests.put(
            _StintRequest(brief=brief, future=future, driver=driver)
        )
        return await future

    async def direct_action(self, intent: pb.Intent, description: str) -> str:
        """Submit one intent on the next tick and report what the world did."""
        if self._reflex_stint is not None:
            return f"{description} -> {INTERRUPTED_BY_REFLEX}"
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        await self._direct_requests.put(
            _DirectRequest(intent=intent, description=description, future=future)
        )
        return await future

    async def wait_ticks(self, ticks: int) -> str:
        """Hold position for `ticks` ticks."""
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        await self._direct_requests.put(
            _DirectRequest(
                intent=pb.Intent(wait=pb.WaitIntent()),
                description=f"wait {ticks} ticks",
                future=future,
                remaining_ticks=max(1, ticks),
            )
        )
        return await future

    async def await_conversation(self) -> ConversationReport | None:
        """Block until the conversation now starting has ended.

        None means the tick loop never saw the actor take a seat, so there is
        nothing to wait for.
        """
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
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._wake_waiters.append(
            _WakeWaiter(
                future=future,
                deadline_tick=self._model.tick + SLEEP_START_GRACE_TICKS,
            )
        )
        return await future

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
        self._note_sleep_transitions(digest)
        if self._model.self_info.asleep:
            await self._sleeping_tick(observation, digest)
            return
        self._expire_wake_waiters()
        # The reflex is checked before anything else: a single-tick action in
        # flight is answered as interrupted rather than with its own outcome.
        self._maybe_start_reflex(digest)
        self._resolve_awaiting_direct(digest)
        self._detect_join(digest)
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
            self._refuse_direct_requests()
            intent = await reflex_stint.decide(digest)
            if reflex_stint.finished:
                self._finish_reflex()
                return intent, None
            return intent, reflex_stint.record_intent_result

        session = self._conversation
        if session is not None:
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

        if self._active_stint is None and not self._stint_requests.empty():
            stint = self._begin_stint(self._stint_requests.get_nowait())
            intent = await stint.decide(digest)
            if stint.finished:
                self._finish_stint()
                return intent, None
            return intent, stint.record_intent_result

        return self._planning_intent()

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
                    f"{request.description} -> {ASLEEP_REJECTION}"
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
            return
        if info.asleep or self._asleep_since < 0:
            return
        record = SleepRecord(
            start_tick=self._asleep_since,
            end_tick=self._model.tick,
            reason=wake_reason(digest),
            fatigue_before=self._sleep_fatigue_before,
            fatigue_after=info.fatigue,
            where=self._sleep_place,
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
        self._release_wake_waiters(record.to_text())

    def _release_wake_waiters(self, text: str) -> None:
        """Answer every parked `sleep` tool with this sleep (or with nothing)."""
        for waiter in list(self._wake_waiters):
            self._wake_waiters.remove(waiter)
            if not waiter.future.done():
                waiter.future.set_result(text)

    def _expire_wake_waiters(self) -> None:
        """Release a `sleep` tool whose sleep never started."""
        for waiter in list(self._wake_waiters):
            if self._model.tick < waiter.deadline_tick:
                continue
            self._wake_waiters.remove(waiter)
            if not waiter.future.done():
                waiter.future.set_result("")

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
            self._refuse_direct_requests()

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
        self._reflex_notes_for_tools.append(line)
        self._reflex_notes_for_prompt.append(line)
        self._release_held_stint(line)
        # The conversation session was never dropped, so it simply resumes; if
        # the world took the seat away it will notice on its next tick.
        self.mode = (
            MODE_CONVERSATION if self._conversation is not None else MODE_PLANNING
        )

    def _refuse_direct_requests(self) -> None:
        """Answer every in-flight and queued single-tick action at once."""
        awaiting = self._awaiting_direct
        if awaiting is not None:
            self._awaiting_direct = None
            if not awaiting.future.done():
                awaiting.future.set_result(
                    f"{awaiting.description} -> {INTERRUPTED_BY_REFLEX}"
                )
        for request in _drain(self._direct_requests):
            if not request.future.done():
                request.future.set_result(
                    f"{request.description} -> {INTERRUPTED_BY_REFLEX}"
                )

    # -- conversations ------------------------------------------------------

    def _detect_join(self, digest: TickDigest) -> None:
        """Enter conversation mode when the world says the actor took a seat."""
        conversation_id = joined_conversation_id(digest)
        if not conversation_id or self._conversation is not None:
            return
        stint = self._active_stint
        if stint is not None:
            stint.finish(END_JOINED_CONVERSATION)
            self._finish_stint(hold=True)
        self._begin_conversation(conversation_id)

    def _begin_conversation(self, conversation_id: str) -> None:
        session = ConversationSession(
            conversation_id,
            self._model,
            self.converser,
            trace=self.trace,
            memory_path=self.planner.memory_path,
            reflex_line=lambda: self.reflex.prompt_line(),
            alert_line=self._alert_line,
        )
        session.begin()
        self._conversation = session
        self.mode = MODE_CONVERSATION
        logger.info("conversation_started", conversation_id=conversation_id)

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
        self._spawn(self._report_conversation(session))

    async def _report_conversation(self, session: ConversationSession) -> None:
        """Write the note, then release everyone waiting on the report."""
        report = await session.write_report()
        text = report.to_text()
        self._release_held_stint(text)
        for waiter in list(self._conversation_waiters):
            self._conversation_waiters.remove(waiter)
            if not waiter.future.done():
                waiter.future.set_result(report)

    def _expire_conversation_waiters(self) -> None:
        """Release a planner tool whose conversation never started."""
        if self._conversation is not None:
            return
        for waiter in list(self._conversation_waiters):
            if self._model.tick < waiter.deadline_tick:
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
        outcome = "submitted"
        for acted in digest.own_actions:
            status = "ok" if acted.success else "failed"
            outcome = f"{acted.action_type} {status}: {acted.details or '(no detail)'}"
            break
        self._awaiting_direct = None
        if not request.future.done():
            request.future.set_result(f"{request.description} -> {outcome}")

    def _fail_pending(self, reason: str) -> None:
        for request in _drain(self._direct_requests):
            if not request.future.done():
                request.future.set_result(f"{request.description} -> {reason}")
        awaiting = self._awaiting_direct
        if awaiting is not None and not awaiting.future.done():
            awaiting.future.set_result(f"{awaiting.description} -> {reason}")
        self._awaiting_direct = None
        for waiter in self._conversation_waiters:
            if not waiter.future.done():
                waiter.future.set_result(None)
        self._conversation_waiters.clear()
        self._release_wake_waiters("")
        for stint_request in _drain(self._stint_requests):
            if not stint_request.future.done():
                stint_request.future.set_exception(RuntimeError(reason))
        held = self._held_stint
        self._held_stint = None
        if held is not None and not held.request.future.done():
            held.request.future.set_result(held.report)

    # -- status -------------------------------------------------------------

    async def _report_status(self) -> None:
        session = self._conversation
        if session is not None:
            status = (
                self.mode,
                "in conversation",
                self.planner.last_thought,
                session.status_json(),
            )
        else:
            stint = self._reflex_stint or self._active_stint
            brief = stint.brief.summary() if stint is not None else ""
            stint_json = stint.status_json() if stint is not None else ""
            status = (self.mode, brief, self.planner.last_thought, stint_json)
        if status == self._last_status:
            return
        self._last_status = status
        try:
            await self.world.report_status(*status)
        except grpc.RpcError as error:
            logger.debug("status_report_failed", details=error.details())


def _drain(queue: "asyncio.Queue[_T]") -> list[_T]:
    items: list[_T] = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


async def run_agent(
    server_address: str,
    entity_id: str,
    *,
    log_root: Path | None = None,
    planner_model: str = "",
    jev_model: str = "",
) -> None:
    """Build every piece and run one actor until it is interrupted."""
    import os

    world = WorldClient(server_address, entity_id)
    jev = TypeSafeJevClient(jev_model or os.environ.get("JEV_MODEL", "jev-latest"))
    agent = JevAgent(
        world, jev, entity_id, log_root=log_root, planner_model=planner_model
    )
    try:
        await agent.run()
    finally:
        await jev.aclose()
        await world.close()
        agent.trace.close()
