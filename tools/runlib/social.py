"""Conversations, giving and reflexes (docs/09_conversation_and_reflex.md)."""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from typing import Iterator, Mapping

from .agenttrace import REFLEX_KIND, AgentTraces
from .report import Moment, deep_link
from .worldscan import ConversationRecord, GiveEvent

# The stint report's stats line, e.g. "stats: hp 12/20, food 5/10 -> hp
# 8/20, food 3/10" (agents/src/agents/jev_agent/stint.py StintReport.to_text).
STINT_HEALTH_RE = re.compile(r"stats: hp (\d+)/\d+.*? -> hp (\d+)/\d+")


def _conversation_events(traces: AgentTraces, event: str) -> Iterator[dict]:
    """One kind of row from an agent's `conversations.jsonl.gz`.

    Runs recorded before this feature shipped have no such file, so an
    absent file simply yields nothing.
    """
    for row in traces.conversations:
        if row.get("event") == event:
            yield row


def summarise_conversations(
    conversations: Mapping[str, ConversationRecord],
    traces: list[AgentTraces],
    viewer_url: str,
    run_id: str,
    hails_succeeded: int = 0,
    hail_failures: Mapping[str, int] | None = None,
    brief_hails_granted: int = 0,
    jev_hails_chosen: int = 0,
) -> dict:
    """The Conversations report section: world facts plus agent-side endings.

    `opened`/`joined`/`utterances`/duration come from the world recording
    (ground truth); `end_reasons` and `notes_written` come from each
    participant's own `conversation_end` report, since ending is a per-actor
    view (docs/09_conversation_and_reflex.md section 4.3).
    """
    end_reasons: Counter[str] = Counter()
    notes_written = 0
    for agent in traces:
        for row in _conversation_events(agent, "conversation_end"):
            end_reasons[row.get("end_reason", "?")] += 1
            # Old runs wrote a single `note` field; newer ones write `agreed`
            # and `commitment` (docs/09_conversation_and_reflex.md section 10,
            # item 5). Either counts as a note written.
            if (
                str(row.get("note", "")).strip()
                or str(row.get("agreed", "")).strip()
                or str(row.get("commitment", "")).strip()
            ):
                notes_written += 1

    # One row per seat taken, so `purpose_total` over-counts conversations
    # with several participants; that is the right denominator here, since a
    # purpose is per-seat, not per-conversation (docs/09 section 10, item 4).
    purpose_total = 0
    purpose_given = 0
    for agent in traces:
        for row in _conversation_events(agent, "conversation_start"):
            purpose_total += 1
            if str(row.get("purpose", "")).strip():
                purpose_given += 1

    records = list(conversations.values())
    opened = sum(1 for record in records if record.opened_tick >= 0)
    opened_by_hail = sum(1 for record in records if record.via_hail)
    failures = dict(hail_failures or {})
    joined = sum(len(record.joins) for record in records)
    distinct_participants = len(
        {participant for record in records for participant in record.participants}
    )
    utterances = sum(record.utterance_count for record in records)
    durations = [record.duration_ticks for record in records if record.end_tick >= 0]
    line_counts = [record.utterance_count for record in records]
    line_count_stats = {
        "min": min(line_counts) if line_counts else 0,
        "median": statistics.median(line_counts) if line_counts else 0,
        "max": max(line_counts) if line_counts else 0,
    }

    longest = sorted(
        records, key=lambda r: (r.duration_ticks, r.utterance_count), reverse=True
    )[:3]

    return {
        "opened": opened,
        "opened_by_hail": opened_by_hail,
        "hails_succeeded": hails_succeeded,
        "hails_attempted": hails_succeeded + sum(failures.values()),
        "hail_failures": failures,
        "brief_hails_granted": brief_hails_granted,
        "jev_hails_chosen": jev_hails_chosen,
        # Whatever Jev did not choose was the planner's own `talk_to`.
        "planner_hails_attempted": max(
            0, hails_succeeded + sum(failures.values()) - jev_hails_chosen
        ),
        "joined": joined,
        "distinct_participants": distinct_participants,
        "utterances": utterances,
        "end_reasons": dict(end_reasons),
        "notes_written": notes_written,
        "purpose_given": purpose_given,
        "purpose_total": purpose_total,
        "mean_duration_ticks": statistics.mean(durations) if durations else 0,
        "mean_lines": statistics.mean(line_counts) if line_counts else 0,
        "line_count_stats": line_count_stats,
        "longest": [
            {
                "conversation_id": record.conversation_id,
                "opened_by": record.opened_by,
                "opened_tick": record.opened_tick,
                "end_tick": record.end_tick,
                "duration_ticks": record.duration_ticks,
                "utterances": record.utterance_count,
                "participants": sorted(record.participants),
                "link": deep_link(
                    viewer_url, run_id, max(record.opened_tick, 0), record.opened_by
                ),
            }
            for record in longest
        ],
    }


def summarise_giving(events: list[GiveEvent]) -> dict:
    """The Giving report section: totals by kind and by giver/receiver pair."""
    kinds: Counter[str] = Counter()
    pairs: Counter[tuple[str, str]] = Counter()
    for event in events:
        kinds[event.kind] += event.amount
        pairs[(event.giver, event.receiver)] += event.amount

    return {
        "count": len(events),
        "kinds": dict(kinds.most_common()),
        "pairs": {
            f"{giver}->{receiver}": amount
            for (giver, receiver), amount in pairs.most_common()
        },
    }


def first_reflex_registration(traces: AgentTraces) -> tuple[int, str]:
    """The tick and instruction digest of the first `set_reflex` call.

    A tick of -1 means the settler never registered one.
    """
    for row in traces.planner:
        if row.get("event") != "tool_call" or row.get("tool") != "set_reflex":
            continue
        args = row.get("args") or {}
        return int(row.get("tick", 0)), str(args.get("instruction", ""))[:80]
    return -1, ""


def summarise_reflexes(traces: list[AgentTraces]) -> tuple[dict, list[Moment]]:
    """The Reflexes report section, plus the moments only it can see.

    Combines `planner.jsonl.gz` (when and what a settler registered) with
    `stints.jsonl.gz` (when the reflex fired, on what trigger, over what it
    interrupted, how it ended, and the health change from its report text).
    A run recorded before `kind` existed on `stint_start` has no reflex
    stints at all, which reports cleanly as zero.
    """
    per_agent: dict[str, dict] = {}
    trigger_counts: Counter[str] = Counter()
    interrupted_counts: Counter[str] = Counter()
    end_reason_counts: Counter[str] = Counter()
    health_deltas: list[int] = []
    ticks_to_first_action: list[int] = []
    moments: list[Moment] = []

    for agent in traces:
        agent_id = agent.agent_id
        registered_tick, digest = first_reflex_registration(agent)
        if registered_tick >= 0:
            per_agent[agent_id] = {"registered_tick": registered_tick, "digest": digest}

        rows = agent.stints
        starts: dict[str, dict] = {
            row["stint_id"]: row
            for row in rows
            if row.get("event") == "stint_start" and row.get("kind") == REFLEX_KIND
        }
        if not starts:
            continue
        ticks_by_stint: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            stint_id = row.get("stint_id", "")
            if stint_id in starts and "action" in row and "top" in row:
                ticks_by_stint[stint_id].append(row)

        for row in rows:
            if row.get("event") != "stint_end":
                continue
            stint_id = row.get("stint_id", "")
            start_row = starts.get(stint_id)
            if start_row is None:
                continue
            trigger = start_row.get("trigger", "?")
            interrupted = start_row.get("interrupted", "?")
            end_reason = row.get("end_reason", "?")
            trigger_counts[trigger] += 1
            interrupted_counts[interrupted] += 1
            end_reason_counts[end_reason] += 1

            match = STINT_HEALTH_RE.search(str(row.get("report", "")))
            if match:
                health_deltas.append(int(match.group(2)) - int(match.group(1)))

            start_tick = int(start_row.get("tick", 0))
            first_action_tick = next(
                (
                    int(tick_row["tick"])
                    for tick_row in ticks_by_stint.get(stint_id, ())
                    if str(tick_row.get("action", "wait")).split(":")[0] != "wait"
                ),
                None,
            )
            if first_action_tick is not None:
                ticks_to_first_action.append(first_action_tick - start_tick)

            if end_reason == "death":
                moments.append(
                    Moment(
                        int(row.get("tick", start_tick)),
                        "reflex_death",
                        agent_id,
                        f"{agent_id} died during a reflex ({trigger})",
                    )
                )
            if interrupted == "conversation":
                moments.append(
                    Moment(
                        start_tick,
                        "reflex_during_conversation",
                        agent_id,
                        f"{agent_id}'s reflex fired mid-conversation ({trigger})",
                    )
                )

    summary = {
        "agents": per_agent,
        "firings_by_trigger": dict(trigger_counts),
        "firings_by_interrupted": dict(interrupted_counts),
        "end_reasons": dict(end_reason_counts),
        "mean_health_change": (statistics.mean(health_deltas) if health_deltas else 0),
        "mean_ticks_to_first_action": (
            statistics.mean(ticks_to_first_action) if ticks_to_first_action else 0
        ),
    }
    return summary, moments
