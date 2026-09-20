"""The stint runner: one Jev call per tick under the code-owned rules.

`Stint` is one machine with one pile of state - the eject and lost streaks, the
remembered food, the no-path streak, the last option, the records. Every rule
reads and writes several of those at once, so they stay methods here rather
than becoming a module of their own.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any, Callable, Mapping, Sequence

import structlog

from ... import world_pb2 as pb
from .. import items
from ..briefs import (
    HAIL_REFUSAL_LIMIT,
    MAX_BRIEF_HAILS,
    Brief,
    BriefHail,
    Option,
    StintDriver,
)
from ..geometry import Coord, chebyshev
from ..jevclient import JevClient, JevDecision
from ..jevstate import StintProgress, build_state, collapse_action
from ..options import (
    HAIL_KEY_PREFIX,
    KEEP_GOING,
    MAX_OPTIONS,
    STEP_KEY_PREFIX,
    brief_object_ids,
    enumerate_options,
    options_to_criteria,
    retreat_option,
    step_target_ids,
)
from ..tracelog import AgentTrace
from ..worldmodel import TickDigest, WorldModel
from .endings import (
    DANGER_HEALTH_FLOOR,
    DANGER_THRESHOLD,
    DONE_OR_STUCK_THRESHOLD,
    EJECT_STREAK_TO_END,
    END_CANCELLED,
    END_DEATH,
    END_FOOD_LOW,
    END_FOOD_ZERO,
    END_LOST,
    END_NO_PATH,
    END_REPEATED_FAILURE,
    END_SUCCESS_OR_JUDGEMENT,
    END_TICKS,
    FOOD_LOW_EXPLANATION,
    FOOD_ZERO_EXPLANATION,
    JEV_TICK_BUDGET_SECONDS,
    LOST_THRESHOLD,
    NO_PATH_EXPLANATION,
    NO_PATH_PATIENCE,
    SLEEP_END_EXPLANATIONS,
    REPEATED_FAILURE_LIMIT,
    STINT_KIND_ORDINARY,
    STINT_KIND_REFLEX,
    never_ends,
)
from .records import StintReport, TickRecord, dedupe, stint_stats

logger = structlog.get_logger(__name__)


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
        end_check: "Callable[[WorldModel], str]" = never_ends,
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
        self._no_path_streak = 0
        # Food as of the previous tick and as of the stint's first tick; -1
        # until the stint has run one tick, so nothing "crosses" on tick one.
        self._food_last = -1
        self._start_food = -1
        self._end_note = ""
        self._failure_action = ""
        self._failure_count = 0
        self._last_option: Option | None = None
        self._started = False
        self._start_position: Coord = model.position
        self._start_stats = stint_stats(model)
        self._start_inventory: dict[str, int] = dict(model.self_info.inventory)
        self._notable: list[str] = []
        # Brief hails already spent, and how often the world refused each one.
        # Both drop a hail out of the option list for the rest of the stint.
        self._hails_done: set[str] = set()
        self._hail_refusals: dict[str, int] = {}

    # -- per-tick -----------------------------------------------------------

    async def decide(self, digest: TickDigest) -> pb.Intent:
        """Choose this tick's intent, ending the stint when a rule fires."""
        if not self._started:
            self._started = True
            self._start_position = self.model.position
            self._start_stats = stint_stats(self.model)
            self._start_inventory = dict(self.model.self_info.inventory)
            self._food_last = self.model.self_info.food
            self._start_food = self._food_last
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
            hails=self._live_hails(),
            places=self.brief.places,
            brief_text=self.brief.text,
            max_options=MAX_OPTIONS,
        )

        no_path = self._no_path_reason(options)
        if no_path:
            self.finish(no_path)
            return pb.Intent(wait=pb.WaitIntent())

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
            hails=self._live_hails(),
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
        self._absorb_hail_outcomes(digest)
        # A sign read during the stint belongs in the report too: the planner
        # gets the same line as a note, and the report says when it was read.
        self._notable.extend(digest.sign_notes)
        if digest.discovered_object_ids:
            self._notable.append(
                f"discovered {len(digest.discovered_object_ids)} new objects"
            )

    def _live_hails(self) -> tuple[BriefHail, ...]:
        """The brief hails still worth offering Jev this tick.

        A hail that landed has done its job, and one the world has refused
        `HAIL_REFUSAL_LIMIT` times is refusing for a reason that will not
        change inside this stint.
        """
        return tuple(
            hail
            for hail in self.brief.hails[:MAX_BRIEF_HAILS]
            if hail.settler not in self._hails_done
            and self._hail_refusals.get(hail.settler, 0) < HAIL_REFUSAL_LIMIT
        )

    def _absorb_hail_outcomes(self, digest: TickDigest) -> None:
        """Fold the world's verdict on the hail Jev submitted into the report.

        Only the previous tick's option says which settler was hailed: a
        converse failure carries the reason and nothing else.
        """
        last = self._last_option
        if last is None or not last.key.startswith(HAIL_KEY_PREFIX):
            return
        settler = last.key[len(HAIL_KEY_PREFIX) :]
        for acted in digest.own_actions:
            if acted.action_type != items.CONVERSE_ACTION_TYPE:
                continue
            if acted.success:
                if not acted.details.startswith(f"{items.ACTION_HAIL} "):
                    continue
                self._hails_done.add(settler)
                self._notable.append(f"hailed {settler} at tick {self.model.tick}")
                continue
            self._hail_refusals[settler] = self._hail_refusals.get(settler, 0) + 1
            self._notable.append(f"hail to {settler} refused: {acted.details}")

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

    def _food_crossing(self) -> str:
        """The end reason when food crossed a threshold on this tick, else `""`.

        Crossing only, so a stint started on an empty stomach is not ended on
        its first tick, and never for a reflex: a reflex runs because a wolf
        is there, and hunger is not what should call it off. Mutates the
        remembered food, so it is asked exactly once per tick.
        """
        food = self.model.self_info.food
        before = self._food_last
        self._food_last = food
        if self.kind == STINT_KIND_REFLEX or before < 0:
            return ""
        info = self.model.self_info
        if food <= 0 < before:
            self._end_note = FOOD_ZERO_EXPLANATION.format(
                max_food=info.max_food,
                tick=self.model.tick,
                damage=items.STARVATION_DAMAGE,
                starve=items.STARVATION_INTERVAL_TICKS,
                health=info.health,
                max_health=info.max_health,
                berry=items.BERRY_FOOD_RESTORE,
            )
            return END_FOOD_ZERO
        if food <= items.FOOD_ALERT_AT < before:
            self._end_note = FOOD_LOW_EXPLANATION.format(
                food=food,
                max_food=info.max_food,
                tick=self.model.tick,
                before=self._start_food,
                interval=items.FOOD_INTERVAL_TICKS,
                damage=items.STARVATION_DAMAGE,
                starve=items.STARVATION_INTERVAL_TICKS,
                berry=items.BERRY_FOOD_RESTORE,
            )
            return END_FOOD_LOW
        return ""

    def _brief_targets(self) -> list[str]:
        """The names and object ids the brief told Jev to walk to."""
        return list(self.brief.places) + brief_object_ids(self.model, self.brief.text)

    def _at_a_brief_target(self, targets: Sequence[str]) -> bool:
        """Whether the body already stands on or next to one of them.

        An arrived-at target gets no step option either, and that is success,
        not a missing route.
        """
        position = self.model.position
        for name in targets:
            place = self.brief.places.get(name)
            if place is not None and chebyshev(place, position) <= 1:
                return True
            obj = self.model.objects.get(name)
            if obj is not None and chebyshev(obj.position, position) <= 1:
                return True
        return False

    def _no_path_reason(self, options: Sequence[Option]) -> str:
        """`no_path` once every brief target has been unreachable long enough."""
        targets = self._brief_targets()
        offered = step_target_ids(options)
        if (
            not targets
            or any(target in offered for target in targets)
            or self._at_a_brief_target(targets)
        ):
            self._no_path_streak = 0
            return ""
        self._no_path_streak += 1
        if self._no_path_streak < NO_PATH_PATIENCE:
            return ""
        self._end_note = NO_PATH_EXPLANATION.format(
            targets=", ".join(targets), ticks=self._no_path_streak
        )
        return END_NO_PATH

    def _termination_reason(self) -> str:
        if not self.model.self_info.alive:
            return END_DEATH
        food_reason = self._food_crossing()
        if food_reason:
            return food_reason
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
        explanation = SLEEP_END_EXPLANATIONS.get(reason, "")
        if explanation:
            self._end_note = explanation.format(tick=self.model.tick)
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
            end_stats=stint_stats(self.model),
            inventory_delta=delta,
            action_counts=counts,
            notable=dedupe(self._notable),
            tail=[record.as_line() for record in self.records[-5:]],
            end_note=self._end_note,
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
