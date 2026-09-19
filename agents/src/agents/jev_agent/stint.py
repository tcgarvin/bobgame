"""A stint: the stretch of ticks during which Jev holds the controls.

The planner hands down a `Brief`; this module turns it into one Jev call per
tick, maps the answer onto a proto Intent, enforces the code-owned rules that
sit on top of Jev's judgement, writes a JSONL trace, and finally compresses the
whole run into a `StintReport` the planner can read.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Protocol, Sequence

import structlog

from .. import world_pb2 as pb
from .geometry import Coord, chebyshev
from .jevclient import JevClient, JevDecision
from .jevstate import StintProgress, build_state, collapse_action
from .pricing import jev_cost_usd
from .options import (
    KEEP_GOING,
    MAX_OPTIONS,
    STEP_KEY_PREFIX,
    Option,
    TravelState,
    brief_object_ids,
    enumerate_options,
    options_to_criteria,
    retreat_option,
    step_target_ids,
)
from .tracelog import AgentTrace
from .worldmodel import TickDigest, WorldModel

logger = structlog.get_logger(__name__)

# Code rules layered on top of Jev's answers. The stint ends when Jev's `done`,
# `stuck` or `lost` probability reaches the threshold on two consecutive ticks.
DONE_OR_STUCK_THRESHOLD = 0.6
LOST_THRESHOLD = 0.6
EJECT_STREAK_TO_END = 2
DANGER_THRESHOLD = 0.8
DANGER_HEALTH_FLOOR = 6
REPEATED_FAILURE_LIMIT = 3

END_SUCCESS_OR_JUDGEMENT = "eject"
# Jev judged that the brief needs something its state does not have: a target
# out of view with no step option toward it, an item it cannot get, a place it
# does not know the way to. The planner has to name or approach it, not retry.
END_LOST = "lost"
LOST_EXPLANATION = (
    "Jev could not see or reach what the brief asked for. Name the target by "
    "object id or as a place in `places`, or move closer first."
)
# The world's intent deadline is 1200 ms after tick start; leave room for the
# gRPC round trip and the state build.
JEV_TICK_BUDGET_SECONDS = 0.9
END_TICKS = "ticks_exhausted"
END_DEATH = "death"
END_REPEATED_FAILURE = "repeated_failure"
END_CANCELLED = "cancelled"
# A reflex stint pre-empted this one (docs/09 section 4.2).
END_PREEMPTED_BY_REFLEX = "reflex"
# The actor took a seat in a conversation, which owns the body from now on.
END_JOINED_CONVERSATION = "joined_conversation"

# `kind` on the `stint_start` trace line: what sort of stint this is.
STINT_KIND_ORDINARY = "stint"
STINT_KIND_REFLEX = "reflex"


def _never_ends(model: "WorldModel") -> str:
    """The default extra end rule: no stint ends because of it."""
    return ""


@dataclass(frozen=True)
class DriverChoice:
    """What a code driver wants to do on one tick."""

    option: Option
    note: str = ""


class StintDriver(Protocol):
    """Code that replaces Jev for a stint, one deterministic decision per tick.

    Used by the planner's `build` tool: laying out walls and roads is arithmetic,
    not judgement, and Jev cannot reason about coordinates tick by tick.
    """

    name: str

    def stop_reason(self, model: WorldModel) -> str:
        """Why the stint should end now, or `""` to carry on."""

    def choose(self, model: WorldModel) -> DriverChoice:
        """The action for this tick. Only called when `stop_reason` was empty."""

    def summary(self) -> str:
        """A few lines of progress for the planner's report."""


@dataclass(frozen=True)
class Brief:
    """What the planner told Jev to do, and the limits it set."""

    instruction: str
    success_condition: str
    max_ticks: int
    notes: str = ""
    check_every: int = 1
    travel: TravelState | None = None
    # The only phrases Jev may shout during this stint; empty means it cannot.
    shouts: tuple[str, ...] = ()
    # The only phrases Jev may say as an invitation to talk; empty means it
    # cannot invite anyone (docs/09 section 8.3).
    invitations: tuple[str, ...] = ()
    # Named destinations Jev may step toward, so it never sees a coordinate.
    places: Mapping[str, Coord] = field(default_factory=dict)

    def summary(self) -> str:
        """One-line form for status reports and the viewer."""
        return f"{self.instruction} (until: {self.success_condition})"

    @property
    def text(self) -> str:
        """Instruction and notes together, for scanning out the ids it names."""
        return f"{self.instruction}\n{self.notes}"

    def as_payload(self) -> dict[str, Any]:
        """The brief as the replay contract serialises it."""
        travel = self.travel
        return {
            "instruction": self.instruction,
            "success_condition": self.success_condition,
            "max_ticks": self.max_ticks,
            "notes": self.notes,
            "check_every": self.check_every,
            "shouts": list(self.shouts),
            "invitations": list(self.invitations),
            "places": {
                name: [target[0], target[1]] for name, target in self.places.items()
            },
            "travel": (
                None
                if travel is None
                else {"target": list(travel.target), "label": travel.label}
            ),
        }


@dataclass(frozen=True)
class TickRecord:
    """One line of the stint's JSONL trace."""

    tick: int
    position: Coord
    health: str
    food: str
    input_tokens: int
    option_count: int
    action: str
    top: Sequence[tuple[str, float]]
    done: float
    stuck: float
    danger: float
    latency_ms: int
    lost: float = 0.0
    confidence: float = 0.0
    probabilities: Mapping[str, float] = field(default_factory=dict)
    intent_result: str = "pending"
    note: str = ""
    driver: str = ""

    def as_payload(self, entity_id: str, stint_id: str) -> dict[str, Any]:
        """Serialise for `stints.jsonl.gz`.

        A driver tick made no Jev call, so it carries none of Jev's numbers:
        writing zeroed latencies and ejects would quietly poison the run
        analysis, which averages every tick row it finds.
        """
        payload: dict[str, Any] = {
            "entity_id": entity_id,
            "stint_id": stint_id,
            "tick": self.tick,
            "position": list(self.position),
            "health": self.health,
            "food": self.food,
            "options": self.option_count,
            "action": self.action,
            "top": [[key, round(value, 3)] for key, value in self.top],
            "intent_result": self.intent_result,
            "note": self.note,
        }
        if self.driver:
            payload["driver"] = self.driver
            return payload
        payload.update(
            {
                "input_tokens": self.input_tokens,
                "cost_usd": jev_cost_usd(self.input_tokens),
                "probabilities": {
                    key: round(value, 3) for key, value in self.probabilities.items()
                },
                "confidence": round(self.confidence, 3),
                "done": round(self.done, 3),
                "stuck": round(self.stuck, 3),
                "lost": round(self.lost, 3),
                "eject": round(max(self.done, self.stuck, self.lost), 3),
                "danger": round(self.danger, 3),
                "latency_ms": self.latency_ms,
            }
        )
        return payload

    def as_line(self) -> str:
        """Compact human form used in the stint report's tail."""
        if self.driver:
            note = f" [{self.note}]" if self.note else ""
            return f"t{self.tick} {self.action} -> {self.intent_result}{note}"
        return (
            f"t{self.tick} {self.action} -> {self.intent_result} "
            f"(done {self.done:.2f}, stuck {self.stuck:.2f}, "
            f"lost {self.lost:.2f}, danger {self.danger:.2f})"
        )


@dataclass
class StintReport:
    """A whole stint compressed to about twenty lines for the planner."""

    brief: Brief
    ticks_used: int
    end_reason: str
    start_position: Coord
    end_position: Coord
    start_stats: str
    end_stats: str
    inventory_delta: Mapping[str, int]
    action_counts: Mapping[str, tuple[int, int]]
    notable: Sequence[str]
    tail: Sequence[str]
    # Blocks appended after the report body: the reflex line for a stint a
    # reflex cut short, and the conversation report for a stint that ended by
    # joining one. The planner's tool call returns all of it in one result.
    appended: list[str] = field(default_factory=list)

    def append(self, text: str) -> None:
        """Add a block of text below the report body."""
        if text:
            self.appended.append(text)

    def to_text(self) -> str:
        """Render the report for the planner's prompt."""
        lines = [
            f"STINT REPORT: {self.brief.instruction}",
            f"  success condition: {self.brief.success_condition}",
            f"  ticks used: {self.ticks_used}/{self.brief.max_ticks}",
            f"  ended because: {self.end_reason}",
        ]
        if self.end_reason == END_LOST:
            lines.append(f"  {LOST_EXPLANATION}")
        lines += [
            f"  position: {self.start_position} -> {self.end_position}",
            f"  stats: {self.start_stats} -> {self.end_stats}",
        ]
        if self.inventory_delta:
            delta = ", ".join(
                f"{kind} {value:+d}"
                for kind, value in sorted(self.inventory_delta.items())
            )
            lines.append(f"  inventory change: {delta}")
        else:
            lines.append("  inventory change: none")
        if self.action_counts:
            lines.append("  actions:")
            for action, (ok, failed) in sorted(self.action_counts.items()):
                lines.append(f"    {action}: {ok} ok, {failed} failed")
        if self.notable:
            lines.append("  notable:")
            lines.extend(f"    {item}" for item in self.notable[:6])
        if self.tail:
            lines.append("  last ticks:")
            lines.extend(f"    {item}" for item in self.tail)
        lines.extend(self.appended)
        return "\n".join(lines)


class Stint:
    """Runs one brief, one tick at a time."""

    def __init__(
        self,
        brief: Brief,
        model: WorldModel,
        jev: JevClient,
        *,
        trace: AgentTrace,
        driver: StintDriver | None = None,
        end_check: "Callable[[WorldModel], str]" = _never_ends,
        kind: str = STINT_KIND_ORDINARY,
        start_fields: Mapping[str, Any] | None = None,
    ) -> None:
        self.brief = brief
        self.end_check = end_check
        self.kind = kind
        self.start_fields = dict(start_fields or {})
        self.model = model
        self.jev = jev
        self.trace = trace
        self.travel = brief.travel
        self.driver = driver

        self.stint_id = ""
        self.finished = False
        self.end_reason = ""
        self.ticks_used = 0
        self.records: list[TickRecord] = []
        self.last_decision = JevDecision(action="")

        self._pending: TickRecord | None = None
        # Index into `records` of a row that has a verdict but is not on disk
        # yet; -1 when there is none. See `_flush_finished_record`.
        self._unflushed = -1
        self._eject_streak = 0
        self._lost_streak = 0
        self._failure_action = ""
        self._failure_count = 0
        self._last_option: Option | None = None
        self._started = False
        self._start_position: Coord = model.position
        self._start_stats = _stats(model)
        self._start_inventory: dict[str, int] = dict(model.self_info.inventory)
        self._notable: list[str] = []

    # -- per-tick -----------------------------------------------------------

    async def decide(self, digest: TickDigest) -> pb.Intent:
        """Choose this tick's intent, ending the stint when a rule fires."""
        if not self._started:
            self._started = True
            self._start_position = self.model.position
            self._start_stats = _stats(self.model)
            self._start_inventory = dict(self.model.self_info.inventory)
            self.stint_id = f"{self.model.entity_id}-{self.model.tick}"
            self.trace.stints.write(
                {
                    "event": "stint_start",
                    "entity_id": self.model.entity_id,
                    "tick": self.model.tick,
                    "stint_id": self.stint_id,
                    "kind": self.kind,
                    "brief": self.brief.as_payload(),
                    **self.start_fields,
                }
            )

        self._absorb(digest)
        if not any(not acted.success for acted in digest.own_actions):
            self._detect_blocked_move()
        # Only now is the previous tick's row final: `_detect_blocked_move`
        # rewrites it, and it used to do so after the row had already gone to
        # disk, so a silently blocked move never reached the trace.
        self._flush_finished_record()

        reason = self._termination_reason()
        if reason:
            self.finish(reason)
            return pb.Intent(wait=pb.WaitIntent())

        if self.driver is not None:
            return self._driven_intent(self.driver)

        options = enumerate_options(
            self.model,
            self.travel,
            shouts=self.brief.shouts,
            invitations=self.brief.invitations,
            places=self.brief.places,
            brief_text=self.brief.text,
            max_options=MAX_OPTIONS,
        )

        if self._should_repeat_last(options):
            option = _find_option(options, self._last_option.key)  # type: ignore[union-attr]
            if option is not None:
                self._commit(option, self.last_decision, len(options), "repeat")
                return option.intent

        state = build_state(
            self.model,
            instruction=self.brief.instruction,
            success_condition=self.brief.success_condition,
            progress=self.progress(),
            notes=self.brief.notes,
            travel=self.travel,
            places=self.brief.places,
            highlight_ids=self._highlight_ids(options),
        )
        criteria = options_to_criteria(options)
        # Written before the call: a timed-out or failed call still saw this
        # state, and the trace should say so.
        self.trace.jev_states.write(
            {
                "entity_id": self.model.entity_id,
                "tick": self.model.tick,
                "stint_id": self.stint_id,
                "state": state,
                "criteria": criteria,
            }
        )

        try:
            decision = await asyncio.wait_for(
                self.jev.decide(state, criteria), timeout=JEV_TICK_BUDGET_SECONDS
            )
        except asyncio.TimeoutError:
            # Better to repeat a sensible action than to miss the deadline.
            logger.warning("jev_call_timed_out", budget_s=JEV_TICK_BUDGET_SECONDS)
            fallback = self._repeat_or_wait(options)
            self._commit(
                fallback, JevDecision(action=fallback.key), len(options), "jev_timeout"
            )
            return fallback.intent
        except (
            Exception
        ) as error:  # noqa: BLE001 - the tick must still produce an intent
            logger.warning("jev_call_failed", error=str(error))
            fallback = _find_option(options, "wait") or options[0]
            self._commit(
                fallback,
                JevDecision(action=fallback.key),
                len(options),
                f"jev_error: {error}",
            )
            return fallback.intent

        self.last_decision = decision
        option = _find_option(options, decision.action)
        note = ""
        if option is None:
            logger.warning("jev_chose_unknown_option", action=decision.action)
            option = _find_option(options, "wait") or options[0]
            note = f"unknown option {decision.action}"

        override = self._danger_override(decision, options)
        if override is not None:
            note = "danger override: retreating"
            option = override

        self._update_eject_streak(decision)
        self._commit(option, decision, len(options), note)
        return option.intent

    def _driven_intent(self, driver: StintDriver) -> pb.Intent:
        """One deterministic tick: no Jev call, no option list, no jev_states line."""
        choice = driver.choose(self.model)
        self._commit(
            choice.option,
            JevDecision(action=choice.option.key),
            0,
            choice.note,
            driver=driver.name,
        )
        return choice.option.intent

    def record_intent_result(self, result: str) -> None:
        """Attach the world's verdict to this tick's record.

        The row is not written yet: next tick's `_detect_blocked_move` may still
        turn an "accepted" move into a "blocked" one, and the trace should say
        what actually happened.
        """
        if self._pending is None:
            return
        record = replace(self._pending, intent_result=result)
        self._pending = None
        self.records.append(record)
        self._unflushed = len(self.records) - 1

    def _flush_finished_record(self) -> None:
        """Write the pending tick row, now that nothing else will change it."""
        if self._unflushed < 0:
            return
        record = self.records[self._unflushed]
        self._unflushed = -1
        self._append_log(record)

    def progress(self) -> StintProgress:
        """What this stint has achieved so far, for Jev's `so_far` block."""
        end_inventory = self.model.self_info.inventory
        change = {
            kind: end_inventory.get(kind, 0) - self._start_inventory.get(kind, 0)
            for kind in set(self._start_inventory) | set(end_inventory)
            if end_inventory.get(kind, 0) != self._start_inventory.get(kind, 0)
        }
        actions: dict[str, int] = {}
        for record in self.records:
            name = collapse_action(record.action)
            actions[name] = actions.get(name, 0) + 1
        position = self.model.position
        return StintProgress(
            ticks_used=self.ticks_used,
            ticks_left=self.brief.max_ticks - self.ticks_used,
            inventory_change=change,
            actions=actions,
            moved_from_start=(
                position[0] - self._start_position[0],
                position[1] - self._start_position[1],
            ),
            net_tiles_moved=chebyshev(position, self._start_position),
        )

    def _highlight_ids(self, options: Sequence[Option]) -> frozenset[str]:
        """Object ids `nearby` must keep: what the brief names or can be walked to."""
        return step_target_ids(options) | frozenset(
            brief_object_ids(self.model, self.brief.text)
        )

    # -- rules --------------------------------------------------------------

    def _absorb(self, digest: TickDigest) -> None:
        """Fold the previous tick's outcome into the stint's counters."""
        for acted in digest.own_actions:
            if acted.success:
                continue
            key = f"{acted.action_type}:{acted.details}"
            if key == self._failure_action:
                self._failure_count += 1
            else:
                self._failure_action = key
                self._failure_count = 1
        if digest.own_actions and all(a.success for a in digest.own_actions):
            self._failure_action = ""
            self._failure_count = 0

        if digest.damage_taken:
            attackers = ", ".join(digest.attackers) or "the world"
            self._notable.append(f"took {digest.damage_taken} damage from {attackers}")
        for utterance in digest.utterances:
            self._notable.append(f'heard {utterance.speaker_id}: "{utterance.text}"')
        for entity_id in digest.deaths:
            self._notable.append(f"{entity_id} died")
        if digest.discovered_object_ids:
            self._notable.append(
                f"discovered {len(digest.discovered_object_ids)} new objects"
            )

    def _detect_blocked_move(self) -> None:
        """A move the world accepted but did not perform leaves us on the same tile.

        The world reports successful moves as events but says nothing about a
        move that lost to an occupied tile, so compare positions ourselves and
        treat it as a failure for the repeat rule and for Jev's recent history.
        """
        if not self.records:
            return
        last = self.records[-1]
        is_move = last.action.startswith(("move_", KEEP_GOING, STEP_KEY_PREFIX))
        if not is_move or last.intent_result != "accepted":
            return
        if last.tick != self.model.tick - 1 or self.model.position != last.position:
            return
        self.records[-1] = replace(last, intent_result="blocked")
        self.model.note_own_outcome(
            f"{last.action} blocked (tile occupied or impassable)"
        )
        key = "move:silently_blocked"
        if key == self._failure_action:
            self._failure_count += 1
        else:
            self._failure_action = key
            self._failure_count = 1

    def _termination_reason(self) -> str:
        if not self.model.self_info.alive:
            return END_DEATH
        if self.ticks_used >= self.brief.max_ticks:
            return END_TICKS
        if self._eject_streak >= EJECT_STREAK_TO_END:
            return END_SUCCESS_OR_JUDGEMENT
        if self._lost_streak >= EJECT_STREAK_TO_END:
            return END_LOST
        if self._failure_count >= REPEATED_FAILURE_LIMIT:
            return END_REPEATED_FAILURE
        extra = self.end_check(self.model)
        if extra:
            return extra
        if self.driver is not None:
            # Asked before the action, so the last tick's record is always
            # flushed by the tick loop before the stint reports.
            return self.driver.stop_reason(self.model)
        return ""

    def _update_eject_streak(self, decision: JevDecision) -> None:
        if max(decision.done, decision.stuck) >= DONE_OR_STUCK_THRESHOLD:
            self._eject_streak += 1
        else:
            self._eject_streak = 0
        # Counted apart from done/stuck so the report can say *why* the body
        # came back: "lost" tells the planner to fix the brief, not the plan.
        if decision.lost >= LOST_THRESHOLD:
            self._lost_streak += 1
        else:
            self._lost_streak = 0

    def _danger_override(
        self, decision: JevDecision, options: Sequence[Option]
    ) -> Option | None:
        if decision.danger <= DANGER_THRESHOLD:
            return None
        if self.model.self_info.health >= DANGER_HEALTH_FLOOR:
            return None
        return retreat_option(self.model, options)

    def _repeat_or_wait(self, options: Sequence[Option]) -> Option:
        """The last option if it is still legal, otherwise wait."""
        if self._last_option is not None:
            again = _find_option(options, self._last_option.key)
            if again is not None:
                return again
        return _find_option(options, "wait") or options[0]

    def _should_repeat_last(self, options: Sequence[Option]) -> bool:
        if self.brief.check_every <= 1 or self._last_option is None:
            return False
        if self.ticks_used % self.brief.check_every == 0:
            return False
        return _find_option(options, self._last_option.key) is not None

    def _commit(
        self,
        option: Option,
        decision: JevDecision,
        option_count: int,
        note: str,
        driver: str = "",
    ) -> None:
        if option.clears_travel:
            self.travel = None
        elif option.travel_target is not None:
            self.travel = option.travel_target

        self._last_option = option
        self.ticks_used += 1
        self._pending = TickRecord(
            tick=self.model.tick,
            position=self.model.position,
            health=f"{self.model.self_info.health}/{self.model.self_info.max_health}",
            food=f"{self.model.self_info.food}/{self.model.self_info.max_food}",
            input_tokens=decision.input_tokens,
            option_count=option_count,
            action=option.key,
            top=decision.top(),
            done=decision.done,
            stuck=decision.stuck,
            lost=decision.lost,
            danger=decision.danger,
            latency_ms=decision.latency_ms,
            confidence=decision.confidence,
            probabilities=dict(decision.probabilities),
            note=note,
            driver=driver,
        )

    # -- finishing ----------------------------------------------------------

    def finish(self, reason: str) -> None:
        """Mark the stint over; the report will say `reason`."""
        if self.finished:
            return
        self.finished = True
        self.end_reason = reason
        self._flush_finished_record()
        logger.info(
            "stint_finished",
            reason=reason,
            ticks=self.ticks_used,
            brief=self.brief.instruction,
        )
        # The end line carries the report, so build it here rather than leaving
        # the reader to reconstruct it from the per-tick records.
        self.trace.stints.write(
            {
                "event": "stint_end",
                "entity_id": self.model.entity_id,
                "tick": self.model.tick,
                "stint_id": self.stint_id,
                "end_reason": reason,
                "ticks_used": self.ticks_used,
                "max_ticks": self.brief.max_ticks,
                "brief": self.brief.instruction,
                "report": self.build_report().to_text(),
            }
        )

    def build_report(self) -> StintReport:
        """Compress the stint for the planner."""
        end_inventory = dict(self.model.self_info.inventory)
        delta: dict[str, int] = {}
        for kind in set(self._start_inventory) | set(end_inventory):
            change = end_inventory.get(kind, 0) - self._start_inventory.get(kind, 0)
            if change:
                delta[kind] = change

        counts: dict[str, tuple[int, int]] = {}
        for record in self.records:
            head = record.action.split(":")[0]
            ok, failed = counts.get(head, (0, 0))
            if record.intent_result == "accepted":
                counts[head] = (ok + 1, failed)
            else:
                counts[head] = (ok, failed + 1)

        return StintReport(
            brief=self.brief,
            ticks_used=self.ticks_used,
            end_reason=self.end_reason or END_CANCELLED,
            start_position=self._start_position,
            end_position=self.model.position,
            start_stats=self._start_stats,
            end_stats=_stats(self.model),
            inventory_delta=delta,
            action_counts=counts,
            notable=_dedupe(self._notable),
            tail=[record.as_line() for record in self.records[-5:]],
        )

    def status_json(self) -> str:
        """The last Jev decision, for `AgentStatusService.ReportStatus`."""
        decision = self.last_decision
        payload: dict[str, Any] = {
            "tick": self.model.tick,
            "stint_id": self.stint_id,
            "brief": self.brief.instruction,
            "success_condition": self.brief.success_condition,
            "notes": self.brief.notes,
            "ticks_used": self.ticks_used,
            "max_ticks": self.brief.max_ticks,
            "action": decision.action,
            "probabilities": {key: round(value, 3) for key, value in decision.top(5)},
            "confidence": round(decision.confidence, 3),
            "done": round(decision.done, 3),
            "stuck": round(decision.stuck, 3),
            "lost": round(decision.lost, 3),
            "eject": round(decision.eject, 3),
            "danger": round(decision.danger, 3),
            "latency_ms": decision.latency_ms,
            "input_tokens": decision.input_tokens,
        }
        return json.dumps(payload)

    def _append_log(self, record: TickRecord) -> None:
        self.trace.stints.write(record.as_payload(self.model.entity_id, self.stint_id))


def _find_option(options: Sequence[Option], key: str) -> Option | None:
    for option in options:
        if option.key == key:
            return option
    return None


def _stats(model: WorldModel) -> str:
    info = model.self_info
    return f"hp {info.health}/{info.max_health}, food {info.food}/{info.max_food}"


def _dedupe(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
