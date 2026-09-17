#!/usr/bin/env python3
"""Summarise a settlement run from the logs directory.

Reads every ``logs/agent-<id>/stints.jsonl`` plus the planner lines in
``logs/agent-<id>.log`` and prints a per-agent and aggregate summary: stint
counts and end reasons, Jev latency and token sizes, action mix, eject and
danger distributions, intent failure rates, planner turn counts and tool
usage, deaths, and the final planner thoughts.

Usage: python tools/analyze_run.py [logs_dir]
"""

import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

TOOL_CALL_RE = re.compile(r"planner_tool_call .*?tool=(\S+)")
TURN_RE = re.compile(r"planner_turn_started")
THOUGHT_RE = re.compile(r"planner_thought .*?text=(.*)$")
FAILED_RE = re.compile(r"planner_turn_failed .*?error=(.*)$")
REJECTED_RE = re.compile(r"intent_rejected .*?reason=(\S+)")


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(q * len(ordered)))
    return ordered[index]


def summarise_agent(agent_id: str, logs_dir: Path) -> dict:
    stints_path = logs_dir / f"agent-{agent_id}" / "stints.jsonl"
    log_path = logs_dir / f"agent-{agent_id}.log"
    rows = load_jsonl(stints_path) if stints_path.exists() else []
    ticks = [r for r in rows if "action" in r and "top" in r]
    ends = [r for r in rows if r.get("event") == "stint_end"]

    actions = Counter(r["action"].split(":")[0] for r in ticks)
    latencies = [r["latency_ms"] for r in ticks if "latency_ms" in r]
    tokens = [r["input_tokens"] for r in ticks if r.get("input_tokens")]
    ejects = [r["eject"] for r in ticks if "eject" in r]
    dangers = [r["danger"] for r in ticks if "danger" in r]
    top_probs = [r["top"][0][1] for r in ticks if r.get("top")]
    failures = sum(1 for r in ticks if r.get("intent_result") not in ("accepted", None))
    end_reasons = Counter(r.get("end_reason", r.get("reason", "?")) for r in ends)

    tools: Counter[str] = Counter()
    turns = 0
    turn_failures = 0
    rejected: Counter[str] = Counter()
    thoughts: list[str] = []
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if TURN_RE.search(line):
                turns += 1
            m = TOOL_CALL_RE.search(line)
            if m:
                tools[m.group(1)] += 1
            m = THOUGHT_RE.search(line)
            if m:
                thoughts.append(m.group(1).strip())
            if FAILED_RE.search(line):
                turn_failures += 1
            m = REJECTED_RE.search(line)
            if m:
                rejected[m.group(1)] += 1

    return {
        "agent": agent_id,
        "stint_ticks": len(ticks),
        "stints": len(ends),
        "end_reasons": dict(end_reasons),
        "actions": dict(actions.most_common()),
        "latency_p50": statistics.median(latencies) if latencies else 0,
        "latency_p95": pct(latencies, 0.95),
        "tokens_mean": statistics.mean(tokens) if tokens else 0,
        "tokens_max": max(tokens) if tokens else 0,
        "eject_mean": statistics.mean(ejects) if ejects else 0,
        "danger_mean": statistics.mean(dangers) if dangers else 0,
        "top_prob_mean": statistics.mean(top_probs) if top_probs else 0,
        "intent_failures": failures,
        "planner_turns": turns,
        "planner_turn_failures": turn_failures,
        "tools": dict(tools.most_common()),
        "rejected": dict(rejected),
        "last_thought": thoughts[-1] if thoughts else "",
        "thoughts": len(thoughts),
    }


def main() -> None:
    logs_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("logs")
    agent_ids = sorted(
        p.name.removeprefix("agent-")
        for p in logs_dir.glob("agent-*")
        if p.is_dir()
    )
    if not agent_ids:
        print(f"no agent logs under {logs_dir}")
        return

    summaries = [summarise_agent(a, logs_dir) for a in agent_ids]
    total_actions: Counter[str] = Counter()
    total_tools: Counter[str] = Counter()
    total_ends: Counter[str] = Counter()
    for s in summaries:
        total_actions.update(s["actions"])
        total_tools.update(s["tools"])
        total_ends.update(s["end_reasons"])

    print(f"== run summary: {len(summaries)} agents ==")
    print(f"stint ticks: {sum(s['stint_ticks'] for s in summaries)}")
    print(f"stints: {sum(s['stints'] for s in summaries)} ends={dict(total_ends)}")
    print(f"planner turns: {sum(s['planner_turns'] for s in summaries)}"
          f" failures={sum(s['planner_turn_failures'] for s in summaries)}")
    print(f"tool calls: {dict(total_tools.most_common())}")
    print(f"jev actions: {dict(total_actions.most_common())}")
    lat = [s["latency_p50"] for s in summaries if s["latency_p50"]]
    if lat:
        print(f"jev latency p50 (median of agents): {statistics.median(lat):.0f} ms")
    print()
    header = f"{'agent':7s} {'ticks':>5s} {'stints':>6s} {'turns':>5s} {'p50ms':>6s} {'p95ms':>6s} {'tok':>5s} {'eject':>5s} {'dang':>5s} {'top':>4s} {'fail':>4s} rejected"
    print(header)
    for s in summaries:
        print(
            f"{s['agent']:7s} {s['stint_ticks']:5d} {s['stints']:6d} {s['planner_turns']:5d} "
            f"{s['latency_p50']:6.0f} {s['latency_p95']:6.0f} {s['tokens_mean']:5.0f} "
            f"{s['eject_mean']:5.2f} {s['danger_mean']:5.2f} {s['top_prob_mean']:4.2f} "
            f"{s['intent_failures']:4d} {s['rejected']}"
        )
    print()
    for s in summaries:
        if s["last_thought"]:
            print(f"[{s['agent']}] {s['last_thought'][:400]}")

    world_log = logs_dir / "world.log"
    if world_log.exists():
        text = world_log.read_text(encoding="utf-8", errors="replace")
        deaths = len(re.findall(r"entity_died|death", text))
        wolves = len(re.findall(r"wolf_spawned", text))
        print()
        print(f"world.log: death mentions={deaths} wolf spawns={wolves}")


if __name__ == "__main__":
    main()
