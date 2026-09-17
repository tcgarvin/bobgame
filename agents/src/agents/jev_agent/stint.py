"""A stint: the stretch of ticks during which Jev holds the controls.

The planner hands down a `Brief`; this module turns it into one Jev call per
tick, maps the answer onto a proto Intent, enforces the code-owned rules that
sit on top of Jev's judgement, writes a JSONL trace, and finally compresses the
whole run into a `StintReport` the planner can read.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

import structlog

from .. import world_pb2 as pb
from .geometry import Coord
from .jevclient import JevClient, JevDecision
from .jevstate import build_state
from .options import (
    MAX_OPTIONS,
    Option,
    TravelState,
    enumerate_options,
    options_to_criteria,
    retreat_option,
)
from .worldmodel import TickDigest, WorldModel

logger = structlog.get_logger(__name__)

# Code rules layered on top of Jev's answers.
EJECT_THRESHOLD = 0.7
EJECT_STREAK_TO_END = 2
DANGER_THRESHOLD = 0.8
DANGER_HEALTH_FLOOR = 6
REPEATED_FAILURE_LIMIT = 3

END_SUCCESS_OR_JUDGEMENT = "eject"
END_TICKS = "ticks_exhausted"
END_DEATH = "death"
END_REPEATED_FAILURE = "repeated_failure"
END_CANCELLED = "cancelled"


@dataclass(frozen=True)
class Brief:
    """What the planner told Jev to do, and the limits it set."""

    instruction: str
    success_condition: str
    max_ticks: int
    notes: str = ""
    check_every: int = 1
    travel: TravelState | None = None

    def summary(self) -> str:
        """One-line form for status reports and the viewer."""
        return f"{self.instruction} (until: {self.success_condition})"


@dataclass(frozen=True)
class TickRecord:
    """One line of the stint's JSONL trace."""

    tick: int
    position: Coord
    health: str
    hunger: str
    input_tokens: int
    option_count: int
    action: str
    top: Sequence[tuple[str, float]]
    eject: float
    danger: float
    latency_ms: int
    intent_result: str = "pending"
    note: str = ""

    def as_json(self, entity_id: str) -> str:
        """Serialise for the JSONL log."""
        return json.dumps(
            {
                "entity_id": entity_id,
                "tick": self.tick,
                "position": list(self.position),
                "health": self.health,
                "hunger": self.hunger,
                "input_tokens": self.input_tokens,
                "options": self.option_count,
                "action": self.action,
                "top": [[key, round(value, 3)] for key, value in self.top],
                "eject": round(self.eject, 3),
                "danger": round(self.danger, 3),
                "latency_ms": self.latency_ms,
                "intent_result": self.intent_result,
                "note": self.note,
            }
        )

    def as_line(self) -> str:
        """Compact human form used in the stint report's tail."""
        return (
            f"t{self.tick} {self.action} -> {self.intent_result} "
            f"(eject {self.eject:.2f}, danger {self.danger:.2f})"
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

    def to_text(self) -> str:
        """Render the report for the planner's prompt."""
        lines = [
            f"STINT REPORT: {self.brief.instruction}",
            f"  success condition: {self.brief.success_condition}",
            f"  ticks used: {self.ticks_used}/{self.brief.max_ticks}",
            f"  ended because: {self.end_reason}",
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
        return "\n".join(lines)


class Stint:
    """Runs one brief, one tick at a time."""

    def __init__(
        self,
        brief: Brief,
        model: WorldModel,
        jev: JevClient,
        *,
        log_path: Path,
    ) -> None:
        self.brief = brief
        self.model = model
        self.jev = jev
        self.log_path = log_path
        self.travel = brief.travel

        self.finished = False
        self.end_reason = ""
        self.ticks_used = 0
        self.records: list[TickRecord] = []
        self.last_decision = JevDecision(action="")

        self._pending: TickRecord | None = None
        self._eject_streak = 0
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

        self._absorb(digest)
        if not any(not acted.success for acted in digest.own_actions):
            self._detect_blocked_move()

        reason = self._termination_reason()
        if reason:
            self.finish(reason)
            return pb.Intent(wait=pb.WaitIntent())

        options = enumerate_options(self.model, self.travel, max_options=MAX_OPTIONS)
        ticks_left = self.brief.max_ticks - self.ticks_used

        if self._should_repeat_last(options):
            option = _find_option(options, self._last_option.key)  # type: ignore[union-attr]
            if option is not None:
                self._commit(option, self.last_decision, len(options), "repeat")
                return option.intent

        state = build_state(
            self.model,
            instruction=self.brief.instruction,
            success_condition=self.brief.success_condition,
            ticks_left=ticks_left,
            notes=self.brief.notes,
            travel=self.travel,
        )
        criteria = options_to_criteria(options)

        try:
            decision = await self.jev.decide(state, criteria)
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

    def record_intent_result(self, result: str) -> None:
        """Attach the world's verdict to this tick's record and flush it to disk."""
        if self._pending is None:
            return
        record = replace(self._pending, intent_result=result)
        self._pending = None
        self.records.append(record)
        self._append_log(record)

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
        is_move = last.action.startswith(("move_", "follow_travel", "travel_to:"))
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
        if self._failure_count >= REPEATED_FAILURE_LIMIT:
            return END_REPEATED_FAILURE
        return ""

    def _update_eject_streak(self, decision: JevDecision) -> None:
        if decision.eject >= EJECT_THRESHOLD:
            self._eject_streak += 1
        else:
            self._eject_streak = 0

    def _danger_override(
        self, decision: JevDecision, options: Sequence[Option]
    ) -> Option | None:
        if decision.danger <= DANGER_THRESHOLD:
            return None
        if self.model.self_info.health >= DANGER_HEALTH_FLOOR:
            return None
        return retreat_option(self.model, options)

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
            hunger=f"{self.model.self_info.hunger}/{self.model.self_info.max_hunger}",
            input_tokens=decision.input_tokens,
            option_count=option_count,
            action=option.key,
            top=decision.top(),
            eject=decision.eject,
            danger=decision.danger,
            latency_ms=decision.latency_ms,
            note=note,
        )

    # -- finishing ----------------------------------------------------------

    def finish(self, reason: str) -> None:
        """Mark the stint over; the report will say `reason`."""
        if self.finished:
            return
        self.finished = True
        self.end_reason = reason
        logger.info(
            "stint_finished",
            reason=reason,
            ticks=self.ticks_used,
            brief=self.brief.instruction,
        )
        self._append_log_line(
            {
                "entity_id": self.model.entity_id,
                "event": "stint_end",
                "tick": self.model.tick,
                "end_reason": reason,
                "ticks_used": self.ticks_used,
                "max_ticks": self.brief.max_ticks,
                "brief": self.brief.instruction,
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
            "brief": self.brief.instruction,
            "ticks_used": self.ticks_used,
            "max_ticks": self.brief.max_ticks,
            "action": decision.action,
            "probabilities": {key: round(value, 3) for key, value in decision.top(5)},
            "confidence": round(decision.confidence, 3),
            "eject": round(decision.eject, 3),
            "danger": round(decision.danger, 3),
            "latency_ms": decision.latency_ms,
            "input_tokens": decision.input_tokens,
        }
        return json.dumps(payload)

    def _append_log(self, record: TickRecord) -> None:
        self._append_log_line(json.loads(record.as_json(self.model.entity_id)))

    def _append_log_line(self, payload: dict[str, Any]) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload) + "\n")
        except OSError as error:
            logger.warning(
                "stint_log_write_failed", path=str(self.log_path), error=str(error)
            )


def _find_option(options: Sequence[Option], key: str) -> Option | None:
    for option in options:
        if option.key == key:
            return option
    return None


def _stats(model: WorldModel) -> str:
    info = model.self_info
    return f"hp {info.health}/{info.max_health}, hunger {info.hunger}/{info.max_hunger}"


def _dedupe(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def default_log_path(entity_id: str, log_root: Path | None = None) -> Path:
    """`logs/agent-<id>/stints.jsonl`, relative to the working directory by default."""
    root = Path("logs") if log_root is None else log_root
    return root / f"agent-{entity_id}" / "stints.jsonl"
