"""What a run's model calls cost (docs/11_cost_accounting.md)."""

from __future__ import annotations

import statistics
from collections import Counter
from datetime import datetime
from typing import TYPE_CHECKING

from .report import Moment, format_usd

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .agenttrace import AgentTraces

# Jev is billed on input tokens only (docs/11_cost_accounting.md). Each run
# writes the price it was billed at to agents/agent-<id>/pricing.json; this is
# the fallback for runs recorded before that file existed.
JEV_USD_PER_MILLION_INPUT_TOKENS = 0.042

# Planner turn records that carry a `usage` block (docs/11, "Trace records").
PLANNER_USAGE_EVENTS = frozenset({"turn_end", "tool_budget_reached"})

# The sleep-time journal (docs/12_sleep_journal.md) is traced in the same file
# as the planner's turns, with its own `usage` block.
JOURNAL_EVENT = "journal_rewrite"
JOURNAL_FAILED_EVENT = "journal_rewrite_failed"

# Stand-in for "no turn": a tick of -1 never matches a real world tick.
NO_TURN: dict = {"tick": -1, "usd": 0.0}


def jev_cost_usd(tick_rows: list[dict], usd_per_million: float) -> float:
    """Jev spend over stint tick rows.

    Rows written since docs/11 carry ``cost_usd``; older rows carry only
    ``input_tokens``, which is priced here at ``usd_per_million``.
    """
    total = 0.0
    for row in tick_rows:
        if "cost_usd" in row:
            total += float(row["cost_usd"])
        elif row.get("input_tokens"):
            total += float(row["input_tokens"]) * usd_per_million / 1e6
    return total


def summarise_planner_cost(rows: list[dict]) -> dict:
    """Planner spend and efficiency from the turn records of one agent.

    Every turn that made model requests carries a ``usage`` block
    (docs/11, "Trace records"). ``cost_usd`` is absent for endpoints that do
    not report a price; such turns set ``cost_missing`` so the total can be
    reported as a lower bound.
    """
    costs: list[float] = []
    requests = 0
    input_tokens = 0
    cached_tokens = 0
    turns = 0
    cost_missing = False
    most_expensive = dict(NO_TURN)

    for row in rows:
        if row.get("event") not in PLANNER_USAGE_EVENTS:
            continue
        usage = row.get("usage") or {}
        if not usage:
            continue
        turns += 1
        requests += int(usage.get("requests", 0))
        input_tokens += int(usage.get("input_tokens", 0))
        cached_tokens += int(usage.get("cached_tokens", 0))
        if row.get("cost_missing"):
            cost_missing = True
        if "cost_usd" not in usage:
            continue
        cost = float(usage["cost_usd"])
        costs.append(cost)
        if cost > most_expensive["usd"]:
            most_expensive = {"tick": int(row.get("tick", 0)), "usd": cost}

    return {
        "planner_usd": float(sum(costs)),
        "planner_cost_available": bool(costs),
        "cost_missing": cost_missing,
        "turns_with_usage": turns,
        "usd_per_turn_mean": statistics.mean(costs) if costs else 0.0,
        "usd_per_turn_max": max(costs) if costs else 0.0,
        "requests_per_turn_mean": requests / turns if turns else 0.0,
        "cached_token_share": cached_tokens / input_tokens if input_tokens else 0.0,
        "most_expensive_turn": most_expensive,
    }


def summarise_journal(rows: list[dict]) -> dict:
    """Journal rewrites, their cost and their failures, for one agent.

    The rewrite records sit in `planner.jsonl.gz` beside the turns
    (docs/12_sleep_journal.md) and carry the same `usage` block, so a rewrite
    that reported no cost simply contributes nothing.
    """
    cost = 0.0
    rewrites = 0
    failures = 0
    truncations: Counter[str] = Counter()
    triggers: Counter[str] = Counter()
    for row in rows:
        event = row.get("event")
        if event == JOURNAL_FAILED_EVENT:
            failures += 1
            continue
        if event != JOURNAL_EVENT:
            continue
        rewrites += 1
        triggers[str(row.get("trigger", "?"))] += 1
        for section in row.get("truncated") or ():
            truncations[str(section)] += 1
        usage = row.get("usage") or {}
        if "cost_usd" in usage:
            cost += float(usage["cost_usd"])
    return {
        "journal_usd": cost,
        "journal_rewrites": rewrites,
        "journal_failures": failures,
        "journal_truncations": dict(truncations.most_common()),
        "journal_triggers": dict(triggers.most_common()),
    }


def converser_cost_usd(rows: list[dict]) -> float:
    """Converser spend: every ``usage`` block in conversations.jsonl.gz.

    Both the per-turn moves and the closing note call are traced there
    (docs/11), so summing over all records covers both.
    """
    total = 0.0
    for row in rows:
        usage = row.get("usage") or {}
        if "cost_usd" in usage:
            total += float(usage["cost_usd"])
    return total


def summarise_cost(traces: "AgentTraces") -> dict:
    """The per-agent ``cost`` object: planner / converser / jev and rates."""
    planner = summarise_planner_cost(traces.planner)
    converser = converser_cost_usd(traces.conversations)
    jev = jev_cost_usd(traces.stint_ticks, traces.jev_price_usd_per_million())
    journal = summarise_journal(traces.planner)
    return {
        "planner_usd": planner["planner_usd"],
        "converser_usd": converser,
        "journal_usd": journal["journal_usd"],
        "jev_usd": jev,
        "total_usd": (
            planner["planner_usd"] + converser + journal["journal_usd"] + jev
        ),
        "journal_rewrites": journal["journal_rewrites"],
        "journal_failures": journal["journal_failures"],
        "journal_truncations": journal["journal_truncations"],
        "journal_triggers": journal["journal_triggers"],
        "planner_cost_available": planner["planner_cost_available"],
        "cost_missing": planner["cost_missing"],
        "turns_with_usage": planner["turns_with_usage"],
        "usd_per_turn_mean": planner["usd_per_turn_mean"],
        "usd_per_turn_max": planner["usd_per_turn_max"],
        "requests_per_turn_mean": planner["requests_per_turn_mean"],
        "cached_token_share": planner["cached_token_share"],
        "most_expensive_turn": planner["most_expensive_turn"],
    }


def run_cost_rates(total_usd: float, meta: dict) -> dict:
    """Dollars per 100 world ticks and per wall-clock hour, where knowable.

    A rate whose inputs the run never recorded is left out of the result
    rather than reported as zero.
    """
    rates: dict[str, float] = {}
    last_tick = meta.get("last_tick")
    if isinstance(last_tick, int) and last_tick > 0:
        rates["usd_per_100_ticks"] = total_usd * 100.0 / last_tick
    started = meta.get("started_at")
    finished = meta.get("finished_at")
    if isinstance(started, str) and isinstance(finished, str):
        hours = (
            datetime.fromisoformat(finished) - datetime.fromisoformat(started)
        ).total_seconds() / 3600.0
        if hours > 0:
            rates["usd_per_hour"] = total_usd / hours
    return rates


def aggregate_cost(summaries: list[dict], meta: dict) -> dict:
    """The run-level ``cost`` object, summed over per-agent cost objects."""
    costs = [s["cost"] for s in summaries]
    planner = sum(c["planner_usd"] for c in costs)
    converser = sum(c["converser_usd"] for c in costs)
    journal = sum(c["journal_usd"] for c in costs)
    jev = sum(c["jev_usd"] for c in costs)
    total = planner + converser + journal + jev
    result = {
        "planner_usd": planner,
        "converser_usd": converser,
        "journal_usd": journal,
        "jev_usd": jev,
        "total_usd": total,
        "journal_rewrites": sum(c["journal_rewrites"] for c in costs),
        "journal_failures": sum(c["journal_failures"] for c in costs),
        "planner_cost_available": any(c["planner_cost_available"] for c in costs),
        "planner_cost_is_lower_bound": any(c["cost_missing"] for c in costs),
        "planner_turns": sum(c["turns_with_usage"] for c in costs),
    }
    result.update(run_cost_rates(total, meta))
    return result


def cost_moments(summaries: list[dict]) -> list[Moment]:
    """The run's single most expensive planner turn, as a notable moment."""
    candidates = [
        (s["cost"]["most_expensive_turn"], s["agent"])
        for s in summaries
        if s["cost"]["most_expensive_turn"]["tick"] >= 0
    ]
    if not candidates:
        return []
    turn, agent_id = max(candidates, key=lambda pair: pair[0]["usd"])
    return [
        Moment(
            turn["tick"],
            "expensive_turn",
            agent_id,
            f"most expensive planner turn: {agent_id} spent "
            f"{format_usd(turn['usd'])}",
        )
    ]
