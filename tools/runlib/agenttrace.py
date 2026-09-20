"""One agent's trace files, read once.

Each agent writes ``stints.jsonl.gz``, ``planner.jsonl.gz``,
``conversations.jsonl.gz`` and a structlog ``agent-<id>.log``
(docs/07_replay.md). :func:`load_agent_traces` reads them all once; every
summary in this package then works off the rows in memory.
"""

from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import cost as cost_module
from .report import Moment
from .runio import RunLayout, iter_jsonl, read_text

# The structlog line the world's gRPC client writes when an intent bounces.
REJECTED_RE = re.compile(r"intent_rejected .*?reason=(\S+)")

REFLEX_KIND = "reflex"


def pct(values: list[float], q: float) -> float:
    """The ``q`` quantile of ``values`` by nearest rank, 0.0 when empty."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(q * len(ordered)))
    return ordered[index]


@dataclass
class AgentTraces:
    """Every recorded row for one agent."""

    agent_id: str
    agent_dir: Path
    stints: list[dict] = field(default_factory=list)
    planner: list[dict] = field(default_factory=list)
    conversations: list[dict] = field(default_factory=list)
    log_text: str = ""

    @property
    def stint_ticks(self) -> list[dict]:
        """The per-tick Jev rows (a decision row carries `action` and `top`)."""
        return [row for row in self.stints if "action" in row and "top" in row]

    def jev_price_usd_per_million(self) -> float:
        """The Jev input-token price this run was billed at (docs/11)."""
        path = self.agent_dir / "pricing.json"
        if not path.exists():
            return cost_module.JEV_USD_PER_MILLION_INPUT_TOKENS
        data = json.loads(path.read_text(encoding="utf-8"))
        return float(
            data.get(
                "jev_usd_per_million_input_tokens",
                cost_module.JEV_USD_PER_MILLION_INPUT_TOKENS,
            )
        )


def load_agent_traces(layout: RunLayout, agent_id: str) -> AgentTraces:
    """Read one agent's trace files. A file a run never wrote reads as empty."""
    agent_dir = layout.agent_dir(agent_id)
    traces = AgentTraces(agent_id=agent_id, agent_dir=agent_dir)
    for attribute, name in (
        ("stints", "stints.jsonl.gz"),
        ("planner", "planner.jsonl.gz"),
        ("conversations", "conversations.jsonl.gz"),
    ):
        path = agent_dir / name
        if path.exists():
            setattr(traces, attribute, list(iter_jsonl(path)))
    traces.log_text = read_text(layout.agents_dir / f"agent-{agent_id}.log")
    return traces


def load_all_traces(layout: RunLayout, agent_ids: list[str]) -> list[AgentTraces]:
    return [load_agent_traces(layout, agent_id) for agent_id in agent_ids]


def summarise_planner(rows: list[dict]) -> dict:
    """Turn counts, tool usage, thoughts and failures from planner.jsonl.gz."""
    tools: Counter[str] = Counter()
    thoughts: list[str] = []
    turns = 0
    turn_failures = 0
    history_resets = 0
    budget_spent = 0
    budget_hard_stops = 0
    for row in rows:
        event = row.get("event")
        if event == "turn_start":
            turns += 1
        elif event == "tool_call":
            tools[row.get("tool", "?")] += 1
        elif event == "turn_end":
            thought = row.get("thought", "")
            if thought:
                thoughts.append(thought.strip())
        elif event == "turn_failed":
            turn_failures += 1
        elif event == "history_reset":
            history_resets += 1
        elif event == "tool_budget_spent":
            budget_spent += 1
        elif event == "tool_budget_reached":
            budget_hard_stops += 1
    return {
        "planner_turns": turns,
        "planner_turn_failures": turn_failures,
        "history_resets": history_resets,
        "budget_spent": budget_spent,
        "budget_hard_stops": budget_hard_stops,
        "tools": dict(tools.most_common()),
        "thoughts": thoughts,
    }


def summarise_agent(traces: AgentTraces) -> dict:
    """The per-agent row of the report, from rows already in memory."""
    ticks = traces.stint_ticks
    ends = [r for r in traces.stints if r.get("event") == "stint_end"]
    starts = [r for r in traces.stints if r.get("event") == "stint_start"]

    actions = Counter(r["action"].split(":")[0] for r in ticks)
    # Brief hails (docs/09 section 9.3): what the planner granted, and how
    # often Jev actually took one of them.
    brief_hails = sum(len(r.get("brief", {}).get("hails", [])) for r in starts)
    jev_hails = sum(1 for r in ticks if str(r["action"]).startswith("hail:"))
    latencies = [r["latency_ms"] for r in ticks if "latency_ms" in r]
    tokens = [r["input_tokens"] for r in ticks if r.get("input_tokens")]
    ejects = [r["eject"] for r in ticks if "eject" in r]
    dangers = [r["danger"] for r in ticks if "danger" in r]
    top_probs = [r["top"][0][1] for r in ticks if r.get("top")]
    confidences = [r["confidence"] for r in ticks if "confidence" in r]
    failures = sum(1 for r in ticks if r.get("intent_result") not in ("accepted", None))
    end_reasons = Counter(r.get("end_reason", r.get("reason", "?")) for r in ends)

    planner = summarise_planner(traces.planner)
    cost = cost_module.summarise_cost(traces)

    rejected: Counter[str] = Counter()
    for line in traces.log_text.splitlines():
        match = REJECTED_RE.search(line)
        if match:
            rejected[match.group(1)] += 1

    thoughts = planner["thoughts"]
    return {
        "agent": traces.agent_id,
        "stint_ticks": len(ticks),
        "stints": len(ends) or len(starts),
        "end_reasons": dict(end_reasons),
        "actions": dict(actions.most_common()),
        "latency_p50": statistics.median(latencies) if latencies else 0,
        "latency_p95": pct(latencies, 0.95),
        "tokens_mean": statistics.mean(tokens) if tokens else 0,
        "tokens_max": max(tokens) if tokens else 0,
        "eject_mean": statistics.mean(ejects) if ejects else 0,
        "danger_mean": statistics.mean(dangers) if dangers else 0,
        "top_prob_mean": statistics.mean(top_probs) if top_probs else 0,
        "confidence_mean": statistics.mean(confidences) if confidences else 0,
        "intent_failures": failures,
        "brief_hails": brief_hails,
        "jev_hails": jev_hails,
        "planner_turns": planner["planner_turns"],
        "planner_turn_failures": planner["planner_turn_failures"],
        "history_resets": planner["history_resets"],
        "budget_spent": planner["budget_spent"],
        "budget_hard_stops": planner["budget_hard_stops"],
        "tools": planner["tools"],
        "rejected": dict(rejected),
        "last_thought": thoughts[-1] if thoughts else "",
        "thoughts": len(thoughts),
        "cost": cost,
    }


def planner_moments(traces: AgentTraces) -> list[Moment]:
    """Planner turn failures and history resets, which need the tick numbers."""
    moments: list[Moment] = []
    for row in traces.planner:
        event = row.get("event")
        tick = int(row.get("tick", 0))
        if event == "turn_failed":
            error = str(row.get("error", ""))[:160]
            moments.append(
                Moment(
                    tick,
                    "planner_failed",
                    traces.agent_id,
                    f"{traces.agent_id} turn failed: {error}",
                )
            )
        elif event == "history_reset":
            moments.append(
                Moment(
                    tick,
                    "history_reset",
                    traces.agent_id,
                    f"{traces.agent_id} planner history reset",
                )
            )
    return moments


def aggregate(summaries: list[dict], meta: dict) -> dict:
    """The run-level totals over the per-agent summaries."""
    total_actions: Counter[str] = Counter()
    total_tools: Counter[str] = Counter()
    total_ends: Counter[str] = Counter()
    for s in summaries:
        total_actions.update(s["actions"])
        total_tools.update(s["tools"])
        total_ends.update(s["end_reasons"])
    latencies = [s["latency_p50"] for s in summaries if s["latency_p50"]]
    return {
        "agents": len(summaries),
        "stint_ticks": sum(s["stint_ticks"] for s in summaries),
        "stints": sum(s["stints"] for s in summaries),
        "end_reasons": dict(total_ends),
        "planner_turns": sum(s["planner_turns"] for s in summaries),
        "planner_turn_failures": sum(s["planner_turn_failures"] for s in summaries),
        "history_resets": sum(s["history_resets"] for s in summaries),
        "brief_hails": sum(s.get("brief_hails", 0) for s in summaries),
        "jev_hails": sum(s.get("jev_hails", 0) for s in summaries),
        "budget_spent": sum(s["budget_spent"] for s in summaries),
        "budget_hard_stops": sum(s["budget_hard_stops"] for s in summaries),
        "tools": dict(total_tools.most_common()),
        "actions": dict(total_actions.most_common()),
        "latency_p50_median": statistics.median(latencies) if latencies else 0,
        "cost": cost_module.aggregate_cost(summaries, meta),
    }
