#!/usr/bin/env python3
"""Summarise a settlement run.

Reads a run directory produced by ``dev.sh`` (``runs/<run_id>/``, see
``docs/07_replay.md``) and prints a per-agent and aggregate summary: stint
counts and end reasons, Jev latency and token sizes, action mix, eject and
danger distributions, intent failure rates, planner turn counts and tool usage,
deaths, the final planner thoughts, and what the run's model calls cost
(docs/11_cost_accounting.md). It then prints a "notable moments"
section: deaths, wolf kills, crafts, placed objects, notes written and planner
trouble, each with a viewer deep link.

The reading and the summaries live in ``tools/runlib``; this file is the
command line over them.

Usage:
    uv run --project tools python tools/analyze_run.py [run_dir]      # default: runs/latest
    uv run --project tools python tools/analyze_run.py --json         # machine-readable dump
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from runlib import agenttrace, cost, report, runio, social, worldscan  # noqa: E402


def build_payload(
    layout: runio.RunLayout,
    run_dir: Path,
    summaries: list[dict],
    totals: dict,
    facts: worldscan.WorldFacts | None,
    conversation_summary: dict,
    giving_summary: dict,
    reflex_summary: dict,
    shown: list[report.Moment],
    omitted: Counter[str],
    viewer_url: str,
) -> dict:
    """The ``--json`` document."""
    return {
        "run_id": layout.run_id,
        "run_dir": str(run_dir.resolve()),
        "meta": layout.meta,
        "totals": totals,
        "agents": summaries,
        "world": facts.as_dict() if facts else None,
        "conversations": conversation_summary,
        "giving": giving_summary,
        "reflexes": reflex_summary,
        "moments": [m.as_dict(viewer_url, layout.run_id) for m in shown],
        "moments_omitted": dict(omitted),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "run_dir",
        nargs="?",
        default=None,
        help="run directory (default: runs/latest)",
    )
    parser.add_argument(
        "--viewer-url",
        default=runio.DEFAULT_VIEWER_URL,
        help=f"base URL for deep links (default: {runio.DEFAULT_VIEWER_URL})",
    )
    parser.add_argument(
        "--max-moments",
        type=int,
        default=report.DEFAULT_MAX_MOMENTS,
        help=f"cap on notable moments (default: {report.DEFAULT_MAX_MOMENTS})",
    )
    parser.add_argument(
        "--json", action="store_true", help="dump the summary and moments as JSON"
    )
    args = parser.parse_args(argv)

    run_dir = runio.resolve_run_dir(args.run_dir)
    if not run_dir.is_dir():
        print(f"no such run directory: {run_dir}", file=sys.stderr)
        return 1

    try:
        layout = runio.load_layout(run_dir)
    except runio.RunLayoutError as error:
        print(str(error), file=sys.stderr)
        return 1

    ids = runio.agent_ids(layout)
    if not ids and layout.ticks_path is None:
        print(f"no agent traces or world recording under {run_dir}", file=sys.stderr)
        return 1

    traces = agenttrace.load_all_traces(layout, ids)
    summaries = [agenttrace.summarise_agent(t) for t in traces]
    totals = agenttrace.aggregate(summaries, layout.meta)
    viewer_url = args.viewer_url.rstrip("/")

    facts: worldscan.WorldFacts | None = None
    moments: list[report.Moment] = []
    conversations: dict[str, worldscan.ConversationRecord] = {}
    giving: list[worldscan.GiveEvent] = []
    if layout.ticks_path is not None:
        scan = worldscan.scan_world(layout.ticks_path)
        facts, moments = scan.facts, scan.moments
        conversations, giving = scan.conversations, scan.giving
    for agent in traces:
        moments.extend(agenttrace.planner_moments(agent))

    conversation_summary = social.summarise_conversations(
        conversations,
        traces,
        viewer_url,
        layout.run_id,
        hails_succeeded=facts.hails_succeeded if facts else 0,
        hail_failures=facts.hail_failures if facts else None,
        brief_hails_granted=sum(s["brief_hails"] for s in summaries),
        jev_hails_chosen=sum(s["jev_hails"] for s in summaries),
    )
    giving_summary = social.summarise_giving(giving)
    reflex_summary, reflex_moments = social.summarise_reflexes(traces)
    moments.extend(reflex_moments)
    moments.extend(cost.cost_moments(summaries))

    shown, omitted = report.select_moments(moments, max(0, args.max_moments))

    if args.json:
        payload = build_payload(
            layout,
            run_dir,
            summaries,
            totals,
            facts,
            conversation_summary,
            giving_summary,
            reflex_summary,
            shown,
            omitted,
            viewer_url,
        )
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    report.print_report(
        layout,
        summaries,
        totals,
        facts,
        conversation_summary,
        giving_summary,
        reflex_summary,
        shown,
        omitted,
        viewer_url,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
