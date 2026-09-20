"""What a stint keeps: one record per tick, and the report the planner reads."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..briefs import Brief
from ..geometry import Coord
from ..pricing import jev_cost_usd
from ..worldmodel import WorldModel
from .endings import END_LOST, LOST_EXPLANATION


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
    # One line of numbers explaining a code-owned end reason (`no_path`,
    # `food_low`, `food_zero`); empty for the reasons that explain themselves.
    end_note: str = ""
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
        if self.end_note:
            lines.append(f"  {self.end_note}")
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


def stint_stats(model: WorldModel) -> str:
    """The two numbers a report opens and closes with."""
    info = model.self_info
    return f"hp {info.health}/{info.max_health}, food {info.food}/{info.max_food}"


def dedupe(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
