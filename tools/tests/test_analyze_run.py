"""Tests for tools/analyze_run.py against a synthetic run directory.

The fixture writes the file layout defined in docs/07_replay.md: meta.json,
world/ticks.jsonl.gz and agents/agent-<id>/{stints,planner}.jsonl.gz.
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import analyze_run  # noqa: E402

RUN_ID = "20260917-143000-settlement"


def write_gz_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def tick_record(tick_id: int, **extra) -> dict:
    record = {
        "type": "tick",
        "tick_id": tick_id,
        "moves": [],
        "entity_updates": [],
        "object_changes": [],
        "objects_added": [],
        "objects_removed": [],
        "actions": [],
        "utterances": [],
        "damage": [],
        "deaths": [],
        "respawns": [],
        "entities_spawned": [],
        "entities_despawned": [],
    }
    record.update(extra)
    return record


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    root = tmp_path / "runs" / RUN_ID
    root.mkdir(parents=True)
    (root / "meta.json").write_text(
        json.dumps(
            {
                "format_version": 1,
                "run_id": RUN_ID,
                "config_name": "settlement",
                "started_at": "2026-09-17T14:30:00-04:00",
                "finished_at": None,
                "last_tick": None,
            }
        ),
        encoding="utf-8",
    )

    write_gz_jsonl(
        root / "world" / "ticks.jsonl.gz",
        [
            tick_record(1),
            tick_record(
                2,
                entities_spawned=[
                    {
                        "entity_id": "wolf_1",
                        "entity_type": "wolf",
                        "position": {"x": 5, "y": 5},
                    }
                ],
            ),
            tick_record(
                3,
                actions=[
                    {
                        "entity_id": "ada",
                        "action_type": "craft",
                        "success": True,
                        "details": "crafted sword",
                    },
                    {
                        "entity_id": "ada",
                        "action_type": "craft",
                        "success": False,
                        "details": "unknown recipe chest",
                    },
                    {
                        "entity_id": "bram",
                        "action_type": "place",
                        "success": True,
                        "details": "placed chest_3 at (6, 6)",
                    },
                    {
                        "entity_id": "bram",
                        "action_type": "write_note",
                        "success": True,
                        "details": "wolves to the north",
                    },
                ],
            ),
            tick_record(
                4,
                damage=[
                    {
                        "entity_id": "wolf_1",
                        "attacker_id": "ada",
                        "amount": 4,
                        "remaining_health": 0,
                    }
                ],
                entities_despawned=[
                    {
                        "entity_id": "wolf_1",
                        "reason": "killed",
                        "position": {"x": 5, "y": 5},
                    }
                ],
            ),
            tick_record(
                5,
                deaths=[
                    {
                        "entity_id": "bram",
                        "killer_id": "wolf_2",
                        "position": {"x": 6, "y": 6},
                    }
                ],
            ),
        ],
    )

    agents = root / "agents"
    write_gz_jsonl(
        agents / "agent-ada" / "stints.jsonl.gz",
        [
            {
                "event": "stint_start",
                "entity_id": "ada",
                "tick": 1,
                "stint_id": "ada-1",
                "brief": {"instruction": "gather wood"},
            },
            {
                "entity_id": "ada",
                "tick": 2,
                "stint_id": "ada-1",
                "action": "extract:tree_1",
                "top": [["extract:tree_1", 0.8]],
                "probabilities": {"extract:tree_1": 0.8, "wait": 0.2},
                "confidence": 0.8,
                "eject": 0.1,
                "danger": 0.2,
                "latency_ms": 200,
                "input_tokens": 2000,
                "intent_result": "accepted",
            },
            {
                "entity_id": "ada",
                "tick": 3,
                "stint_id": "ada-1",
                "action": "craft:sword",
                "top": [["craft:sword", 0.6]],
                "confidence": 0.6,
                "eject": 0.3,
                "danger": 0.1,
                "latency_ms": 400,
                "input_tokens": 2100,
                "intent_result": "rejected",
            },
            {
                "event": "stint_end",
                "entity_id": "ada",
                "tick": 4,
                "stint_id": "ada-1",
                "end_reason": "eject",
                "ticks_used": 3,
                "max_ticks": 20,
                "brief": {},
                "report": "done",
            },
        ],
    )
    write_gz_jsonl(
        agents / "agent-ada" / "planner.jsonl.gz",
        [
            {
                "event": "turn_start",
                "entity_id": "ada",
                "tick": 1,
                "turn": 1,
                "prompt": "…",
            },
            {
                "event": "tool_call",
                "entity_id": "ada",
                "tick": 1,
                "turn": 1,
                "tool": "look",
                "args": {},
            },
            {
                "event": "turn_end",
                "entity_id": "ada",
                "tick": 4,
                "turn": 1,
                "thought": "Gathered wood.",
                "tool_calls": 1,
                "duration_ms": 900,
            },
            {
                "event": "turn_failed",
                "entity_id": "ada",
                "tick": 5,
                "turn": 2,
                "error": "model returned no tool call",
            },
            {"event": "history_reset", "entity_id": "ada", "tick": 5, "turn": 2},
        ],
    )
    (agents / "agent-ada.log").write_text(
        "2026-09-17T04:50:40Z [info] intent_rejected reason=dead entity_id=ada\n",
        encoding="utf-8",
    )
    return root


def analyse(run_dir: Path, *args: str) -> dict:
    """Run the CLI in --json mode and return the parsed payload."""
    import io
    import contextlib

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        exit_code = analyze_run.main([str(run_dir), "--json", *args])
    assert exit_code == 0
    return json.loads(buffer.getvalue())


def moments_by_kind(payload: dict) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for moment in payload["moments"]:
        grouped.setdefault(moment["kind"], []).append(moment)
    return grouped


def test_reads_run_metadata(run_dir: Path) -> None:
    payload = analyse(run_dir)
    assert payload["run_id"] == RUN_ID
    assert payload["meta"]["config_name"] == "settlement"


def test_world_facts(run_dir: Path) -> None:
    world = analyse(run_dir)["world"]
    assert world["ticks"] == 5
    assert world["first_tick"] == 1
    assert world["last_tick"] == 5
    assert world["wolves_spawned"] == 1
    assert world["wolves_despawned"] == {"killed": 1}
    assert world["deaths"] == {"bram": 1}
    # Wolf ids collapse to "wolf": which wolf bit you is never interesting.
    assert world["killers"] == {"wolf": 1}
    assert world["crafts"] == {"sword": 1}  # the failed craft is not counted
    assert world["placements"] == {"chest": 1}
    assert world["notes_written"] == 1


def test_agent_metrics_from_gzip_traces(run_dir: Path) -> None:
    ada = analyse(run_dir)["agents"][0]
    assert ada["agent"] == "ada"
    assert ada["stint_ticks"] == 2
    assert ada["stints"] == 1
    assert ada["end_reasons"] == {"eject": 1}
    assert ada["actions"] == {"extract": 1, "craft": 1}
    assert ada["intent_failures"] == 1
    assert ada["latency_p50"] == 300
    assert ada["confidence_mean"] == pytest.approx(0.7)
    assert ada["rejected"] == {"dead": 1}


def test_planner_file_beats_log_regex(run_dir: Path) -> None:
    ada = analyse(run_dir)["agents"][0]
    assert ada["planner_turns"] == 1
    assert ada["planner_turn_failures"] == 1
    assert ada["history_resets"] == 1
    assert ada["tools"] == {"look": 1}
    assert ada["last_thought"] == "Gathered wood."


def test_notable_moments_and_links(run_dir: Path) -> None:
    payload = analyse(run_dir)
    grouped = moments_by_kind(payload)
    assert set(grouped) == {
        "first_wolf",
        "craft",
        "place",
        "write_note",
        "wolf_killed",
        "death",
        "planner_failed",
        "history_reset",
    }
    assert grouped["death"][0]["tick"] == 5
    assert grouped["death"][0]["entity_id"] == "bram"
    assert (
        grouped["death"][0]["link"]
        == f"http://localhost:5173/?run={RUN_ID}&tick=5&entity=bram"
    )
    # The wolf kill is attributed to the attacker from the same tick's damage.
    assert grouped["wolf_killed"][0]["entity_id"] == "ada"
    assert grouped["wolf_killed"][0]["tick"] == 4
    ticks = [m["tick"] for m in payload["moments"]]
    assert ticks == sorted(ticks)


def test_viewer_url_override(run_dir: Path) -> None:
    payload = analyse(run_dir, "--viewer-url", "http://box:9999/")
    assert payload["moments"][0]["link"].startswith("http://box:9999/?run=")


def test_max_moments_caps_and_reports_omissions(run_dir: Path) -> None:
    payload = analyse(run_dir, "--max-moments", "2")
    assert len(payload["moments"]) == 2
    kinds = {m["kind"] for m in payload["moments"]}
    assert kinds == {"death", "wolf_killed"}  # rarest kinds kept
    assert sum(payload["moments_omitted"].values()) == 6


def test_truncated_gzip_is_tolerated(run_dir: Path) -> None:
    path = run_dir / "agents" / "agent-ada" / "stints.jsonl.gz"
    data = path.read_bytes()
    path.write_bytes(data[: len(data) - 12])
    payload = analyse(run_dir)
    # Reading stops at the truncation instead of raising.
    assert payload["agents"][0]["stint_ticks"] >= 0


def test_legacy_logs_layout(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    agent_dir = logs / "agent-bob"
    agent_dir.mkdir(parents=True)
    (agent_dir / "stints.jsonl").write_text(
        json.dumps(
            {
                "entity_id": "bob",
                "tick": 1,
                "action": "wait",
                "top": [["wait", 0.9]],
                "eject": 0.1,
                "danger": 0.0,
                "latency_ms": 100,
                "input_tokens": 500,
                "intent_result": "accepted",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (logs / "agent-bob.log").write_text(
        "[info] planner_turn_started entity_id=bob\n"
        "[info] planner_tool_call tool=look entity_id=bob\n"
        "[info] planner_thought text=looking around\n",
        encoding="utf-8",
    )
    payload = analyse(logs)
    assert payload["meta"] == {}
    assert payload["world"] is None
    assert payload["moments"] == []
    bob = payload["agents"][0]
    assert bob["stint_ticks"] == 1
    assert bob["planner_turns"] == 1
    assert bob["tools"] == {"look": 1}
    assert bob["last_thought"] == "looking around"


def _action(entity: str, action_type: str, details: str) -> dict:
    return {
        "entity_id": entity,
        "action_type": action_type,
        "success": True,
        "details": details,
    }


def test_building_actions_are_counted_and_only_firsts_become_moments(
    tmp_path: Path,
) -> None:
    ticks = tmp_path / "ticks.jsonl.gz"
    write_gz_jsonl(
        ticks,
        [
            tick_record(
                1,
                actions=[
                    _action("ada", "craft", "crafted plank x2"),
                    _action("ada", "craft", "crafted workshop_table"),
                    _action("bram", "craft", "crafted bed"),
                ],
            ),
            tick_record(
                2,
                actions=[
                    _action("ada", "place", "placed wood_wall_7 at (3, 4)"),
                    _action("bram", "place", "placed wood_wall_8 at (3, 5)"),
                    _action("bram", "place", "placed bed_9 at (4, 5)"),
                ],
            ),
            tick_record(
                3,
                actions=[
                    _action("bram", "rest", "rested at bed_9 (+2 health)"),
                    _action("ada", "extract", "dismantling wood_wall_7 (1/3)"),
                    _action("ada", "extract", "dismantled wood_wall_7 (+1 wood_wall)"),
                    _action("ada", "extract", "extracted wood from tree_1"),
                ],
            ),
        ],
    )

    facts, moments, _conversations, _giving = analyze_run.scan_world_ticks(ticks)

    assert facts.crafts == {"plank": 1, "workshop_table": 1, "bed": 1}
    assert facts.workshop_crafts == 1
    assert facts.placements == {"wood_wall": 2, "bed": 1}
    assert facts.dismantles == {"wood_wall": 1}
    assert facts.rests == 1
    milestones = [m.text for m in moments if m.kind == "milestone"]
    assert milestones == [
        "first wood_wall: ada placed wood_wall_7 at (3, 4)",
        "first bed: bram placed bed_9 at (4, 5)",
    ]
    assert [m for m in moments if m.kind in ("craft", "place")] == []


# --------------------------------------------------------------------------
# conversations and giving: world facts
# --------------------------------------------------------------------------


def _utterance(speaker: str, conversation_id: str, text: str = "hi") -> dict:
    return {
        "speaker_id": speaker,
        "channel": "conversation",
        "text": text,
        "position": {"x": 0, "y": 0},
        "conversation_id": conversation_id,
    }


def test_conversation_and_give_world_facts(tmp_path: Path) -> None:
    ticks = tmp_path / "ticks.jsonl.gz"
    write_gz_jsonl(
        ticks,
        [
            tick_record(
                1,
                actions=[_action("mira", "converse", "open conv_1")],
                utterances=[_utterance("mira", "conv_1", "hello")],
            ),
            tick_record(
                2,
                actions=[_action("theo", "converse", "join conv_1")],
                utterances=[_utterance("theo", "conv_1", "hi there")],
            ),
            tick_record(
                3,
                actions=[_action("theo", "give", "gave 2 stone to mira")],
            ),
            tick_record(4, objects_removed=["conv_1"]),
            # A second conversation that nobody ever joins.
            tick_record(5, actions=[_action("kai", "converse", "open conv_2")]),
        ],
    )

    facts, moments, conversations, giving = analyze_run.scan_world_ticks(ticks)

    assert set(conversations) == {"conv_1", "conv_2"}
    conv1 = conversations["conv_1"]
    assert conv1.opened_tick == 1
    assert conv1.opened_by == "mira"
    assert conv1.joins == [(2, "theo")]
    assert conv1.participants == {"mira", "theo"}
    assert conv1.utterance_count == 2
    assert conv1.end_tick == 4
    assert conv1.duration_ticks == 3

    conv2 = conversations["conv_2"]
    assert conv2.joins == []
    assert conv2.end_tick == -1

    assert giving == [
        analyze_run.GiveEvent(
            tick=3, giver="theo", receiver="mira", kind="stone", amount=2
        )
    ]

    kinds = {m.kind for m in moments}
    assert "conversation_joined" in kinds
    assert "conversation_give" in kinds
    give_moment = next(m for m in moments if m.kind == "conversation_give")
    assert give_moment.tick == 3
    assert give_moment.entity_id == "theo"
    assert "conv_1" in give_moment.text


def _local_utterance(speaker: str, text: str, open_to_talk: bool = False) -> dict:
    return {
        "speaker_id": speaker,
        "channel": "local",
        "text": text,
        "position": {"x": 0, "y": 0},
        "open_to_talk": open_to_talk,
    }


def _failed_action(entity: str, action_type: str, details: str) -> dict:
    return {
        "entity_id": entity,
        "action_type": action_type,
        "success": False,
        "details": details,
    }


def test_hails_are_counted_with_their_refusals(tmp_path: Path) -> None:
    """docs/09 section 9: `hail conv_N <target>` and `hailed conv_N <hailer>`."""
    ticks = tmp_path / "ticks.jsonl.gz"
    write_gz_jsonl(
        ticks,
        [
            tick_record(
                1,
                actions=[
                    _action("theo", "converse", "hail conv_9 mira"),
                    _action("mira", "converse", "hailed conv_9 theo"),
                ],
            ),
            tick_record(
                2,
                actions=[
                    _failed_action(
                        "kai",
                        "converse",
                        "mira was in a conversation 14 ticks ago and cannot be "
                        "hailed for another 46 ticks",
                    ),
                    _failed_action("kai", "converse", "theo is asleep"),
                    # Shared with `accept`, so deliberately not attributed.
                    _failed_action("kai", "converse", "not next to mira"),
                ],
            ),
            tick_record(3, objects_removed=["conv_9"]),
        ],
    )

    facts, moments, conversations, _giving = analyze_run.scan_world_ticks(ticks)

    assert facts.hails_succeeded == 1
    assert facts.hail_failures == {"hail cooldown": 1, "target asleep": 1}

    conv = conversations["conv_9"]
    assert conv.opened_by == "theo"
    assert conv.via_hail is True
    assert conv.via_invitation is False
    assert conv.participants == {"mira", "theo"}
    assert conv.joins == [], "the hailed settler is not a joiner"

    hail_moment = next(m for m in moments if m.kind == "hail")
    assert hail_moment.tick == 1
    assert hail_moment.entity_id == "theo"
    assert "mira" in hail_moment.text and "conv_9" in hail_moment.text
    assert "conversation_joined" not in {m.kind for m in moments}

    layout = analyze_run.RunLayout(
        run_id="r",
        root=tmp_path,
        agents_dir=tmp_path / "agents",
        ticks_path=ticks,
        meta={},
    )
    summary = analyze_run.summarise_conversations(
        conversations,
        ["mira", "theo"],
        layout,
        "http://localhost:5173",
        "r",
        hails_succeeded=facts.hails_succeeded,
        hail_failures=facts.hail_failures,
    )
    assert summary["opened_by_hail"] == 1
    assert summary["hails_succeeded"] == 1
    assert summary["hails_attempted"] == 3
    assert summary["hail_failures"] == {"hail cooldown": 1, "target asleep": 1}


def test_invitation_said_and_accepted(tmp_path: Path) -> None:
    """docs/09 section 8: a flagged `say` and an `accept` of it.

    The inviter's own action for an accept is "join conv_N" (same text as an
    ordinary join, section 8.2); this must not also produce a
    `conversation_joined` moment for it.
    """
    ticks = tmp_path / "ticks.jsonl.gz"
    write_gz_jsonl(
        ticks,
        [
            tick_record(
                1,
                utterances=[
                    _local_utterance("mira", "anyone want to talk?", open_to_talk=True)
                ],
            ),
            tick_record(
                2,
                actions=[
                    _action("theo", "converse", "accept conv_5 mira"),
                    _action("mira", "converse", "join conv_5"),
                ],
            ),
            tick_record(3, objects_removed=["conv_5"]),
        ],
    )

    facts, moments, conversations, _giving = analyze_run.scan_world_ticks(ticks)

    assert facts.invitations_said == 1

    assert set(conversations) == {"conv_5"}
    conv = conversations["conv_5"]
    assert conv.opened_by == "mira"
    assert conv.via_invitation is True
    assert conv.participants == {"mira", "theo"}
    # The inviter's own "join conv_5" action must not be counted as a joiner.
    assert conv.joins == []

    kinds = {m.kind for m in moments}
    assert "invitation_accepted" in kinds
    assert "conversation_joined" not in kinds
    accepted_moment = next(m for m in moments if m.kind == "invitation_accepted")
    assert accepted_moment.tick == 2
    assert accepted_moment.entity_id == "theo"
    assert "mira" in accepted_moment.text
    assert "conv_5" in accepted_moment.text

    layout = analyze_run.RunLayout(
        run_id="r",
        root=tmp_path,
        agents_dir=tmp_path / "agents",
        ticks_path=ticks,
        meta={},
    )
    summary = analyze_run.summarise_conversations(
        conversations,
        ["mira", "theo"],
        layout,
        "http://localhost:5173",
        "r",
        invitations_said=facts.invitations_said,
    )
    assert summary["invitations_said"] == 1
    assert summary["opened_by_invitation"] == 1


def test_summarise_conversations_and_giving(tmp_path: Path) -> None:
    ticks = tmp_path / "ticks.jsonl.gz"
    write_gz_jsonl(
        ticks,
        [
            tick_record(
                10,
                actions=[_action("mira", "converse", "open conv_9")],
                utterances=[_utterance("mira", "conv_9")],
            ),
            tick_record(11, actions=[_action("theo", "converse", "join conv_9")]),
            tick_record(20, objects_removed=["conv_9"]),
        ],
    )
    _facts, _moments, conversations, giving = analyze_run.scan_world_ticks(ticks)

    layout = analyze_run.RunLayout(
        run_id="r",
        root=tmp_path,
        agents_dir=tmp_path / "agents",
        ticks_path=ticks,
        meta={},
    )
    write_gz_jsonl(
        layout.agents_dir / "agent-mira" / "conversations.jsonl.gz",
        [
            {
                "event": "conversation_start",
                "entity_id": "mira",
                "tick": 10,
                "conversation_id": "conv_9",
                "purpose": "ask theo for stone",
            },
            {
                "event": "conversation_end",
                "entity_id": "mira",
                "tick": 20,
                "conversation_id": "conv_9",
                "end_reason": "closed",
                "agreed": "theo wants to trade stone",
            },
        ],
    )
    write_gz_jsonl(
        layout.agents_dir / "agent-theo" / "conversations.jsonl.gz",
        [
            {
                "event": "conversation_start",
                "entity_id": "theo",
                "tick": 11,
                "conversation_id": "conv_9",
            },
            {
                "event": "conversation_end",
                "entity_id": "theo",
                "tick": 20,
                "conversation_id": "conv_9",
                "end_reason": "left",
                "agreed": "",
                "commitment": "",
            },
        ],
    )

    summary = analyze_run.summarise_conversations(
        conversations, ["mira", "theo"], layout, "http://localhost:5173", "r"
    )
    assert summary["opened"] == 1
    assert summary["joined"] == 1
    assert summary["distinct_participants"] == 2
    assert summary["utterances"] == 1
    assert summary["end_reasons"] == {"closed": 1, "left": 1}
    assert summary["notes_written"] == 1
    assert summary["mean_duration_ticks"] == 10
    assert summary["line_count_stats"] == {"min": 1, "median": 1, "max": 1}
    # One seat (mira's) of the two taken carried a purpose; theo joined with
    # none, as a joiner has no purpose to give (docs/09 section 10, item 4).
    assert summary["purpose_given"] == 1
    assert summary["purpose_total"] == 2
    assert len(summary["longest"]) == 1
    assert summary["longest"][0]["conversation_id"] == "conv_9"
    assert summary["longest"][0]["link"].startswith("http://localhost:5173/?run=r")

    giving_summary = analyze_run.summarise_giving(giving)
    assert giving_summary == {"count": 0, "kinds": {}, "pairs": {}}


def test_summarise_giving_aggregates_kinds_and_pairs() -> None:
    events = [
        analyze_run.GiveEvent(1, "mira", "theo", "stone", 2),
        analyze_run.GiveEvent(2, "mira", "theo", "wood", 1),
        analyze_run.GiveEvent(3, "theo", "mira", "stone", 1),
    ]
    summary = analyze_run.summarise_giving(events)
    assert summary["count"] == 3
    assert summary["kinds"] == {"stone": 3, "wood": 1}
    assert summary["pairs"] == {"mira->theo": 3, "theo->mira": 1}


# --------------------------------------------------------------------------
# reflexes
# --------------------------------------------------------------------------


def _reflex_layout(tmp_path: Path) -> "analyze_run.RunLayout":
    return analyze_run.RunLayout(
        run_id="r",
        root=tmp_path,
        agents_dir=tmp_path / "agents",
        ticks_path=None,
        meta={},
    )


def test_summarise_reflexes_reports_firings_and_health_change(
    tmp_path: Path,
) -> None:
    layout = _reflex_layout(tmp_path)
    write_gz_jsonl(
        layout.agents_dir / "agent-mira" / "planner.jsonl.gz",
        [
            {
                "event": "tool_call",
                "entity_id": "mira",
                "tick": 5,
                "turn": 1,
                "tool": "set_reflex",
                "args": {"instruction": "Attack the wolf if adjacent."},
            },
        ],
    )
    write_gz_jsonl(
        layout.agents_dir / "agent-mira" / "stints.jsonl.gz",
        [
            {
                "event": "stint_start",
                "entity_id": "mira",
                "tick": 10,
                "stint_id": "mira-10",
                "kind": "reflex",
                "trigger": "wolf_near",
                "interrupted": "planning",
                "brief": {},
            },
            {
                "entity_id": "mira",
                "tick": 11,
                "stint_id": "mira-10",
                "action": "attack:wolf_1",
                "top": [["attack:wolf_1", 0.9]],
                "intent_result": "accepted",
            },
            {
                "event": "stint_end",
                "entity_id": "mira",
                "tick": 12,
                "stint_id": "mira-10",
                "end_reason": "death",
                "report": "STINT REPORT: ...\n  stats: hp 10/20, food 5/10 "
                "-> hp 0/20, food 5/10",
            },
        ],
    )
    write_gz_jsonl(
        layout.agents_dir / "agent-theo" / "stints.jsonl.gz",
        [
            {
                "event": "stint_start",
                "entity_id": "theo",
                "tick": 20,
                "stint_id": "theo-20",
                "kind": "reflex",
                "trigger": "damage",
                "interrupted": "conversation",
                "brief": {},
            },
            {
                "entity_id": "theo",
                "tick": 21,
                "stint_id": "theo-20",
                "action": "wait",
                "top": [["wait", 1.0]],
                "intent_result": "accepted",
            },
            {
                "entity_id": "theo",
                "tick": 22,
                "stint_id": "theo-20",
                "action": "attack:wolf_2",
                "top": [["attack:wolf_2", 0.7]],
                "intent_result": "accepted",
            },
            {
                "event": "stint_end",
                "entity_id": "theo",
                "tick": 23,
                "stint_id": "theo-20",
                "end_reason": "threat_gone",
                "report": "STINT REPORT: ...\n  stats: hp 15/20, food 4/10 "
                "-> hp 13/20, food 4/10",
            },
            # An ordinary stint must not be counted as a reflex.
            {
                "event": "stint_start",
                "entity_id": "theo",
                "tick": 30,
                "stint_id": "theo-30",
                "kind": "stint",
                "brief": {},
            },
            {
                "event": "stint_end",
                "entity_id": "theo",
                "tick": 35,
                "stint_id": "theo-30",
                "end_reason": "eject",
                "report": "STINT REPORT: ...\n  stats: hp 13/20, food 4/10 "
                "-> hp 12/20, food 3/10",
            },
        ],
    )

    summary, moments = analyze_run.summarise_reflexes(["mira", "theo"], layout)

    assert summary["agents"]["mira"]["registered_tick"] == 5
    assert "Attack the wolf" in summary["agents"]["mira"]["digest"]
    assert "theo" not in summary["agents"]
    assert summary["firings_by_trigger"] == {"wolf_near": 1, "damage": 1}
    assert summary["firings_by_interrupted"] == {"planning": 1, "conversation": 1}
    assert summary["end_reasons"] == {"death": 1, "threat_gone": 1}
    assert summary["mean_health_change"] == pytest.approx((-10 + -2) / 2)
    # mira acts on the tick right after the trigger (11 - 10); theo's reflex
    # waits one extra tick before attacking (22 - 20).
    assert summary["mean_ticks_to_first_action"] == pytest.approx((1 + 2) / 2)

    kinds = {m.kind for m in moments}
    assert kinds == {"reflex_death", "reflex_during_conversation"}
    death_moment = next(m for m in moments if m.kind == "reflex_death")
    assert death_moment.entity_id == "mira"
    assert death_moment.tick == 12
    mid_conv_moment = next(m for m in moments if m.kind == "reflex_during_conversation")
    assert mid_conv_moment.entity_id == "theo"
    assert mid_conv_moment.tick == 20


# --------------------------------------------------------------------------
# pre-feature runs: no conversations file, no "kind" on stint_start
# --------------------------------------------------------------------------


def test_pre_conversation_run_reports_cleanly(run_dir: Path) -> None:
    """`run_dir` predates conversations/reflexes: no file, no "kind" key."""
    payload = analyse(run_dir)

    assert payload["conversations"] == {
        "opened": 0,
        "opened_by_invitation": 0,
        "invitations_said": 0,
        "opened_by_hail": 0,
        "hails_succeeded": 0,
        "hails_attempted": 0,
        "brief_hails_granted": 0,
        "jev_hails_chosen": 0,
        "planner_hails_attempted": 0,
        "hail_failures": {},
        "joined": 0,
        "distinct_participants": 0,
        "utterances": 0,
        "end_reasons": {},
        "notes_written": 0,
        "purpose_given": 0,
        "purpose_total": 0,
        "mean_duration_ticks": 0,
        "mean_lines": 0,
        "line_count_stats": {"min": 0, "median": 0, "max": 0},
        "longest": [],
    }
    assert payload["giving"] == {"count": 0, "kinds": {}, "pairs": {}}
    assert payload["reflexes"] == {
        "agents": {},
        "firings_by_trigger": {},
        "firings_by_interrupted": {},
        "end_reasons": {},
        "mean_health_change": 0,
        "mean_ticks_to_first_action": 0,
    }
    kinds = {m["kind"] for m in payload["moments"]}
    assert "reflex_death" not in kinds
    assert "conversation_joined" not in kinds


# --------------------------------------------------------------------------
# metal tier and sleep (docs/10_metal_and_sleep.md, section 7)
# --------------------------------------------------------------------------


def _clock(day: int, tick_of_day: int, day_length: int = 300) -> dict:
    return {
        "day": day,
        "tick_of_day": tick_of_day,
        "day_length": day_length,
        "night": tick_of_day >= day_length * 2 // 3,
    }


def _entity_update(entity_id: str, asleep: bool = False) -> dict:
    return {
        "entity_id": entity_id,
        "position": {"x": 0, "y": 0},
        "asleep": asleep,
        "fatigue": 0,
        "max_fatigue": 100,
        "sleeping_on": "",
        "collapsed": False,
    }


def test_metal_tier_counts_and_moments(tmp_path: Path) -> None:
    ticks = tmp_path / "ticks.jsonl.gz"
    write_gz_jsonl(
        ticks,
        [
            tick_record(
                1,
                actions=[
                    _action("ada", "craft", "crafted furnace"),
                    _action("ada", "craft", "crafted charcoal x2"),
                ],
            ),
            tick_record(
                2, actions=[_action("ada", "place", "placed furnace_2 at (3, 4)")]
            ),
            tick_record(3, actions=[_action("ada", "craft", "crafted copper_ingot")]),
            tick_record(4, actions=[_action("bram", "craft", "crafted iron_ingot")]),
            tick_record(
                5,
                actions=[
                    _action("ada", "craft", "crafted copper_axe"),
                    _action("bram", "craft", "crafted iron_sword"),
                ],
            ),
            tick_record(
                6,
                actions=[
                    _action("ada", "extract", "worked copper_vein_1 (+1 copper_ore)"),
                    _action("bram", "extract", "worked iron_vein_1 (+1 iron_ore)"),
                    _action("bram", "extract", "worked iron_vein_1 (+1 iron_ore)"),
                ],
            ),
            tick_record(
                7, actions=[_action("bram", "place", "placed anvil_9 at (5, 5)")]
            ),
        ],
    )

    facts, moments, _conversations, _giving = analyze_run.scan_world_ticks(ticks)

    assert facts.smelts == {"charcoal": 1, "copper_ingot": 1, "iron_ingot": 1}
    assert facts.stations_placed == {"furnace": 1, "anvil": 1}
    assert facts.metal_tools_crafted == {"copper_axe": 1, "iron_sword": 1}
    assert facts.vein_yields == {"copper_ore": 1, "iron_ore": 2}

    milestones = {m.text for m in moments if m.kind == "milestone"}
    assert "first furnace: ada placed furnace_2 at (3, 4)" in milestones
    assert "first anvil: bram placed anvil_9 at (5, 5)" in milestones
    assert "first ingot: ada crafted copper_ingot" in milestones
    assert "first iron tool: bram crafted iron_sword" in milestones

    # Smelting products and metal tools don't spam a "craft" moment per item.
    craft_moments = [m for m in moments if m.kind == "craft"]
    assert craft_moments == []


def test_sleep_wake_and_collapse_counts(tmp_path: Path) -> None:
    ticks = tmp_path / "ticks.jsonl.gz"
    write_gz_jsonl(
        ticks,
        [
            tick_record(1, actions=[_action("ada", "sleep", "asleep on bed_1")]),
            tick_record(2, actions=[_action("bram", "sleep", "asleep on the ground")]),
            tick_record(3, actions=[_action("ada", "wake", "woke up: rested")]),
            tick_record(4, actions=[_action("bram", "wake", "woke up: hungry")]),
            tick_record(
                5, actions=[_action("kai", "collapse", "collapsed from exhaustion")]
            ),
            tick_record(
                6, actions=[_action("theo", "collapse", "collapsed from exhaustion")]
            ),
        ],
    )

    facts, moments, _conversations, _giving = analyze_run.scan_world_ticks(ticks)

    assert facts.sleeps == {"bed": 1, "ground": 1}
    assert facts.wakes == {"rested": 1, "hungry": 1}
    assert facts.collapses == 2

    collapse_moments = [
        m for m in moments if m.kind == "milestone" and "collapse" in m.text
    ]
    # Only the first collapse becomes a notable moment.
    assert len(collapse_moments) == 1
    assert collapse_moments[0].entity_id == "kai"
    assert collapse_moments[0].tick == 5


def test_first_death_while_asleep_moment(tmp_path: Path) -> None:
    ticks = tmp_path / "ticks.jsonl.gz"
    write_gz_jsonl(
        ticks,
        [
            tick_record(
                1,
                clock=_clock(0, 50),
                entity_updates=[
                    _entity_update("ada", asleep=True),
                    _entity_update("bram"),
                ],
            ),
            # ada dies while still asleep going into this tick; the world
            # clears `asleep` the same tick it kills the entity, so the
            # entity_updates in *this* record already show it False.
            tick_record(
                2,
                clock=_clock(0, 51),
                entity_updates=[
                    _entity_update("ada", asleep=False),
                    _entity_update("bram"),
                ],
                deaths=[{"entity_id": "ada", "killer_id": "wolf_1"}],
            ),
            # bram dies wide awake: no moment for this one.
            tick_record(
                3,
                clock=_clock(0, 52),
                deaths=[{"entity_id": "bram", "killer_id": "wolf_2"}],
            ),
        ],
    )

    facts, moments, _conversations, _giving = analyze_run.scan_world_ticks(ticks)

    assert facts.sleep_data_available is True
    asleep_death = [m for m in moments if m.kind == "milestone" and "asleep" in m.text]
    assert len(asleep_death) == 1
    assert asleep_death[0].entity_id == "ada"
    assert asleep_death[0].tick == 2
    assert "wolf_1" in asleep_death[0].text


def test_night_midpoint_asleep_counts(tmp_path: Path) -> None:
    ticks = tmp_path / "ticks.jsonl.gz"
    rows = [tick_record(0, clock=_clock(0, 0))]
    # Night is tick_of_day 200..299 for a 300-tick day; three snapshots with
    # a rising then falling asleep count so the midpoint pick is meaningful.
    for i, (tod, asleep_count) in enumerate([(200, 1), (250, 4), (299, 2)]):
        entity_updates = [
            _entity_update(f"s{n}", asleep=(n < asleep_count)) for n in range(5)
        ]
        rows.append(
            tick_record(200 + i, clock=_clock(0, tod), entity_updates=entity_updates)
        )
    write_gz_jsonl(ticks, rows)

    facts, _moments, _conversations, _giving = analyze_run.scan_world_ticks(ticks)

    assert facts.sleep_data_available is True
    assert facts.night_midpoints == [{"day": 0, "tick": 201, "asleep": 4}]


def test_sleep_section_unavailable_before_docs_10(run_dir: Path) -> None:
    """`run_dir`'s ticks have no `clock`/fatigue fields at all."""
    payload = analyse(run_dir)
    world = payload["world"]
    assert world["sleep_data_available"] is False
    assert world["night_midpoints"] == []
    assert world["sleeps"] == {}
    assert world["collapses"] == 0
    assert world["wakes"] == {}
    assert world["smelts"] == {}
    assert world["stations_placed"] == {}
    assert world["metal_tools_crafted"] == {}
    assert world["vein_yields"] == {}


# --------------------------------------------------------------------------
# cost accounting (docs/11_cost_accounting.md)
# --------------------------------------------------------------------------

COST_RUN_ID = "20260918-090000-settlement"


@pytest.fixture
def cost_run_dir(tmp_path: Path) -> Path:
    """A run whose traces carry the docs/11 cost fields.

    `mira` is the fully-instrumented case (planner cost, converser cost, Jev
    `cost_usd`, its own `pricing.json`); `theo` is the awkward case: a turn
    whose endpoint reported no price, and Jev rows with tokens only.
    """
    root = tmp_path / "runs" / COST_RUN_ID
    root.mkdir(parents=True)
    (root / "meta.json").write_text(
        json.dumps(
            {
                "format_version": 1,
                "run_id": COST_RUN_ID,
                "config_name": "settlement",
                "started_at": "2026-09-18T09:00:00-04:00",
                "finished_at": "2026-09-18T10:00:00-04:00",
                "last_tick": 200,
            }
        ),
        encoding="utf-8",
    )
    write_gz_jsonl(root / "world" / "ticks.jsonl.gz", [tick_record(1)])

    agents = root / "agents"
    (agents / "agent-mira").mkdir(parents=True)
    (agents / "agent-mira" / "pricing.json").write_text(
        json.dumps(
            {
                "jev_usd_per_million_input_tokens": 0.05,
                "planner_model": "openrouter:qwen/qwen3.7-flash",
            }
        ),
        encoding="utf-8",
    )
    write_gz_jsonl(
        agents / "agent-mira" / "stints.jsonl.gz",
        [
            {
                "event": "stint_start",
                "entity_id": "mira",
                "tick": 1,
                "stint_id": "mira-1",
                "brief": {},
            },
            {
                "entity_id": "mira",
                "tick": 2,
                "stint_id": "mira-1",
                "action": "wait",
                "top": [["wait", 0.9]],
                "latency_ms": 200,
                "input_tokens": 20_000,
                "cost_usd": 0.001,
                "intent_result": "accepted",
            },
            {
                "entity_id": "mira",
                "tick": 3,
                "stint_id": "mira-1",
                "action": "wait",
                "top": [["wait", 0.9]],
                "latency_ms": 200,
                "input_tokens": 40_000,
                "cost_usd": 0.002,
                "intent_result": "accepted",
            },
        ],
    )
    write_gz_jsonl(
        agents / "agent-mira" / "planner.jsonl.gz",
        [
            {"event": "turn_start", "entity_id": "mira", "tick": 10, "turn": 1},
            {
                "event": "turn_end",
                "entity_id": "mira",
                "tick": 10,
                "turn": 1,
                "thought": "gathering",
                "usage": {
                    "input_tokens": 100_000,
                    "output_tokens": 500,
                    "cached_tokens": 50_000,
                    "requests": 10,
                    "cost_usd": 0.01,
                },
            },
            {"event": "turn_start", "entity_id": "mira", "tick": 20, "turn": 2},
            {
                "event": "tool_budget_reached",
                "entity_id": "mira",
                "tick": 20,
                "turn": 2,
                "usage": {
                    "input_tokens": 300_000,
                    "output_tokens": 1_500,
                    "cached_tokens": 150_000,
                    "requests": 30,
                    "cost_usd": 0.03,
                },
            },
            {
                "event": "journal_rewrite",
                "entity_id": "mira",
                "tick": 25,
                "trigger": "sleep",
                "sections": {"Me": 120, "Tomorrow": 90},
                "truncated": ["Story so far"],
                "duration_ms": 4200,
                "model": "openrouter:qwen/qwen3.7-flash",
                "usage": {"input_tokens": 9_000, "requests": 1, "cost_usd": 0.002},
            },
            {
                "event": "journal_rewrite",
                "entity_id": "mira",
                "tick": 40,
                "trigger": "death",
                "sections": {"Me": 100},
                "truncated": [],
                "duration_ms": 3000,
                "model": "openrouter:qwen/qwen3.7-flash",
                "usage": {"input_tokens": 8_000, "requests": 1, "cost_usd": 0.001},
            },
        ],
    )
    write_gz_jsonl(
        agents / "agent-mira" / "conversations.jsonl.gz",
        [
            {
                "event": "turn",
                "entity_id": "mira",
                "tick": 12,
                "conversation_id": "conv_1",
                "usage": {"input_tokens": 4_000, "requests": 1, "cost_usd": 0.0005},
            },
            # A cancelled turn made no call, so it carries no usage at all.
            {
                "event": "turn",
                "entity_id": "mira",
                "tick": 13,
                "conversation_id": "conv_1",
            },
            {
                "event": "conversation_end",
                "entity_id": "mira",
                "tick": 14,
                "conversation_id": "conv_1",
                "end_reason": "closed",
                "note": "theo wants stone",
                "usage": {"input_tokens": 2_000, "requests": 1, "cost_usd": 0.0002},
            },
        ],
    )

    write_gz_jsonl(
        agents / "agent-theo" / "stints.jsonl.gz",
        [
            {
                "entity_id": "theo",
                "tick": 4,
                "stint_id": "theo-4",
                "action": "wait",
                "top": [["wait", 0.9]],
                "latency_ms": 200,
                "input_tokens": 2_000_000,
                "intent_result": "accepted",
            },
        ],
    )
    write_gz_jsonl(
        agents / "agent-theo" / "planner.jsonl.gz",
        [
            {"event": "turn_start", "entity_id": "theo", "tick": 30, "turn": 1},
            {
                "event": "turn_end",
                "entity_id": "theo",
                "tick": 30,
                "turn": 1,
                "thought": "no price reported",
                "cost_missing": True,
                "usage": {
                    "input_tokens": 1_000,
                    "output_tokens": 10,
                    "cached_tokens": 0,
                    "requests": 1,
                },
            },
            {
                "event": "journal_rewrite_failed",
                "entity_id": "theo",
                "tick": 35,
                "trigger": "sleep",
                "error": "no route to host",
                "duration_ms": 900,
            },
        ],
    )
    return root


def test_per_agent_cost_rollup(cost_run_dir: Path) -> None:
    agents = {a["agent"]: a["cost"] for a in analyse(cost_run_dir)["agents"]}
    mira = agents["mira"]
    assert mira["planner_usd"] == pytest.approx(0.04)
    assert mira["converser_usd"] == pytest.approx(0.0007)
    # Jev rows carry cost_usd, so pricing.json is not consulted.
    assert mira["jev_usd"] == pytest.approx(0.003)
    assert mira["journal_usd"] == pytest.approx(0.003)
    assert mira["total_usd"] == pytest.approx(0.0467)
    assert mira["planner_cost_available"] is True
    assert mira["cost_missing"] is False
    assert mira["turns_with_usage"] == 2
    assert mira["usd_per_turn_mean"] == pytest.approx(0.02)
    assert mira["usd_per_turn_max"] == pytest.approx(0.03)
    assert mira["requests_per_turn_mean"] == pytest.approx(20.0)
    assert mira["cached_token_share"] == pytest.approx(0.5)
    assert mira["most_expensive_turn"] == {"tick": 20, "usd": pytest.approx(0.03)}


def test_jev_cost_from_tokens_uses_run_price(cost_run_dir: Path) -> None:
    agents = {a["agent"]: a["cost"] for a in analyse(cost_run_dir)["agents"]}
    # theo has no pricing.json, so the module constant prices its tokens.
    assert agents["theo"]["jev_usd"] == pytest.approx(
        2.0 * analyze_run.JEV_USD_PER_MILLION_INPUT_TOKENS
    )
    assert agents["theo"]["planner_usd"] == 0.0
    assert agents["theo"]["planner_cost_available"] is False
    assert agents["theo"]["cost_missing"] is True
    assert agents["theo"]["most_expensive_turn"] == {"tick": -1, "usd": 0.0}


def test_run_level_cost_totals_and_rates(cost_run_dir: Path) -> None:
    cost = analyse(cost_run_dir)["totals"]["cost"]
    jev = 0.003 + 2.0 * analyze_run.JEV_USD_PER_MILLION_INPUT_TOKENS
    total = 0.04 + 0.0007 + 0.003 + jev
    assert cost["planner_usd"] == pytest.approx(0.04)
    assert cost["converser_usd"] == pytest.approx(0.0007)
    assert cost["jev_usd"] == pytest.approx(jev)
    assert cost["journal_usd"] == pytest.approx(0.003)
    assert cost["total_usd"] == pytest.approx(total)
    assert cost["planner_cost_available"] is True
    assert cost["planner_cost_is_lower_bound"] is True
    assert cost["planner_turns"] == 3
    assert cost["usd_per_100_ticks"] == pytest.approx(total * 100 / 200)
    assert cost["usd_per_hour"] == pytest.approx(total)


def test_cost_rates_skipped_when_meta_incomplete() -> None:
    complete = {
        "started_at": "2026-09-18T09:00:00-04:00",
        "finished_at": "2026-09-18T10:00:00-04:00",
        "last_tick": 50,
    }
    rates = analyze_run.run_cost_rates(1.0, complete)
    assert rates["usd_per_hour"] == pytest.approx(1.0)
    assert rates["usd_per_100_ticks"] == pytest.approx(2.0)

    unfinished = analyze_run.run_cost_rates(
        1.0, {"started_at": complete["started_at"], "finished_at": None}
    )
    assert unfinished == {}
    assert analyze_run.run_cost_rates(1.0, {}) == {}


def test_most_expensive_turn_is_a_notable_moment(cost_run_dir: Path) -> None:
    payload = analyse(cost_run_dir)
    expensive = [m for m in payload["moments"] if m["kind"] == "expensive_turn"]
    assert len(expensive) == 1
    assert expensive[0]["tick"] == 20
    assert expensive[0]["entity_id"] == "mira"
    assert "$0.0300" in expensive[0]["text"]
    assert (
        expensive[0]["link"]
        == f"http://localhost:5173/?run={COST_RUN_ID}&tick=20&entity=mira"
    )


def test_old_run_reports_planner_cost_unavailable(run_dir: Path) -> None:
    """`run_dir` predates docs/11: tokens on Jev rows, no usage on turns."""
    payload = analyse(run_dir)
    ada = payload["agents"][0]["cost"]
    assert ada["planner_cost_available"] is False
    assert ada["planner_usd"] == 0.0
    assert ada["cost_missing"] is False
    assert ada["jev_usd"] == pytest.approx(
        4_100 * analyze_run.JEV_USD_PER_MILLION_INPUT_TOKENS / 1e6
    )
    cost = payload["totals"]["cost"]
    assert cost["planner_cost_available"] is False
    assert cost["planner_cost_is_lower_bound"] is False
    assert cost["total_usd"] == pytest.approx(ada["jev_usd"])
    # meta.json has no finished_at and no last_tick, so neither rate is given.
    assert "usd_per_hour" not in cost
    assert "usd_per_100_ticks" not in cost


def test_cost_section_prints_na_for_old_runs(
    run_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = analyze_run.main([str(run_dir)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "== cost" in out
    assert "planner: n/a" in out
    assert "rates: unavailable" in out


def test_dollar_formatting_switches_at_one_dollar() -> None:
    assert analyze_run.format_usd(0.12345) == "$0.1235"
    assert analyze_run.format_usd(0.0) == "$0.0000"
    assert analyze_run.format_usd(1.0) == "$1.00"
    assert analyze_run.format_usd(12.345) == "$12.35"


# --------------------------------------------------------------------------
# the sleep-time journal (docs/12_sleep_journal.md)
# --------------------------------------------------------------------------


def test_journal_rewrites_are_counted_and_billed(cost_run_dir: Path) -> None:
    payload = analyse(cost_run_dir)
    agents = {a["agent"]: a["cost"] for a in payload["agents"]}

    mira = agents["mira"]
    assert mira["journal_rewrites"] == 2
    assert mira["journal_failures"] == 0
    assert mira["journal_triggers"] == {"sleep": 1, "death": 1}
    assert mira["journal_truncations"] == {"Story so far": 1}

    theo = agents["theo"]
    assert theo["journal_rewrites"] == 0
    assert theo["journal_failures"] == 1
    assert theo["journal_usd"] == pytest.approx(0.0)

    totals = payload["totals"]["cost"]
    assert totals["journal_rewrites"] == 2
    assert totals["journal_failures"] == 1


def test_journal_section_and_cost_column_are_printed(
    cost_run_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert analyze_run.main([str(cost_run_dir)]) == 0
    out = capsys.readouterr().out

    assert "journal: $0.0030" in out
    assert "== journal (docs/12_sleep_journal.md) ==" in out
    assert "rewrites: 2  failures: 1" in out
    assert "sleep x1, death x1" in out
    assert "Story so far x1" in out


def test_a_run_without_journal_rewrites_says_so(
    run_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert analyze_run.main([str(run_dir)]) == 0
    out = capsys.readouterr().out

    assert "no journal rewrites recorded" in out


# --------------------------------------------------------------------------
# signs (docs/08_building.md, "Signs")
# --------------------------------------------------------------------------


def test_sign_writes_are_counted_and_become_moments(tmp_path: Path) -> None:
    ticks = tmp_path / "ticks.jsonl.gz"
    write_gz_jsonl(
        ticks,
        [
            tick_record(
                1,
                actions=[
                    _action("ada", "place", "placed sign_4 at (3, 4)"),
                    _action("ada", "write_note", 'wrote sign_4: "wolves to the north"'),
                ],
            ),
            tick_record(
                2,
                actions=[
                    _action("bram", "write_note", 'wrote sign_4: "all clear"'),
                    _action("bram", "write_note", "cleared sign_4"),
                    _action("bram", "write_note", "wrote slot 0 on board_1"),
                ],
            ),
        ],
    )

    facts, moments, _conversations, _giving = analyze_run.scan_world_ticks(ticks)

    assert facts.placements == {"sign": 1}
    assert facts.signs_written == 2
    assert facts.signs_cleared == 1
    # A board note is still a board note.
    assert facts.notes_written == 1
    assert [m.text for m in moments if m.kind == "sign"] == [
        'ada wrote on sign_4: "wolves to the north"',
        'bram wrote on sign_4: "all clear"',
    ]
    assert [m.text for m in moments if m.kind == "milestone"] == [
        "first sign: ada placed sign_4 at (3, 4)"
    ]


# --------------------------------------------------------------------------
# brief hails (docs/09_conversation_and_reflex.md section 9.3)
# --------------------------------------------------------------------------


def test_brief_hails_and_jev_hail_choices_are_counted(tmp_path: Path) -> None:
    """A brief that grants a hail, and a tick where Jev took it."""
    root = tmp_path / "20260919-000000-settlement"
    (root / "agents").mkdir(parents=True)
    write_gz_jsonl(
        root / "agents" / "agent-ada" / "stints.jsonl.gz",
        [
            {
                "event": "stint_start",
                "entity_id": "ada",
                "tick": 1,
                "stint_id": "ada-1",
                "brief": {
                    "instruction": "gather stone",
                    "hails": [{"settler": "dov", "line": "Split the wall work?"}],
                },
            },
            {
                "entity_id": "ada",
                "tick": 2,
                "stint_id": "ada-1",
                "action": "hail:dov",
                "top": [["hail:dov", 0.7]],
                "intent_result": "accepted",
            },
        ],
    )
    layout = analyze_run.RunLayout(
        run_id=root.name,
        root=root,
        agents_dir=root / "agents",
        ticks_path=None,
        meta={},
    )

    summary = analyze_run.summarise_agent("ada", layout)

    assert summary["brief_hails"] == 1
    assert summary["jev_hails"] == 1
    assert summary["actions"]["hail"] == 1


# -- settler deaths and wolf kills are different things (2026-09-20) ---------


def _update(entity_id: str, entity_type: str, food: int = 50) -> dict:
    return {
        "entity_id": entity_id,
        "entity_type": entity_type,
        "position": {"x": 0, "y": 0},
        "food": food,
        "asleep": False,
    }


def test_a_wolf_dying_is_a_kill_not_a_death(tmp_path: Path) -> None:
    ticks = tmp_path / "ticks.jsonl.gz"
    write_gz_jsonl(
        ticks,
        [
            tick_record(
                1,
                entity_updates=[
                    _update("esme", "player"),
                    _update("wolf_1", "wolf"),
                    _update("wolf_2", "wolf"),
                ],
                deaths=[
                    {"entity_id": "wolf_1", "killer_id": "esme"},
                    {"entity_id": "wolf_2", "killer_id": "esme"},
                ],
            )
        ],
    )

    facts, moments, _conversations, _giving = analyze_run.scan_world_ticks(ticks)

    assert facts.deaths == {}
    assert facts.killers == {}
    assert facts.wolf_kills == {"esme": 2}
    assert [m for m in moments if m.kind == "death"] == []


def test_a_settler_killed_by_a_wolf_is_counted_under_wolf(tmp_path: Path) -> None:
    ticks = tmp_path / "ticks.jsonl.gz"
    write_gz_jsonl(
        ticks,
        [
            tick_record(
                1,
                entity_updates=[_update("ada", "player"), _update("wolf_9", "wolf")],
                deaths=[{"entity_id": "ada", "killer_id": "wolf_9"}],
            )
        ],
    )

    facts, moments, _conversations, _giving = analyze_run.scan_world_ticks(ticks)

    assert facts.deaths == {"ada": 1}
    assert facts.killers == {"wolf": 1}
    assert facts.wolf_kills == {}
    assert [m.text for m in moments if m.kind == "death"] == ["ada killed by wolf_9"]


def test_an_unattributed_death_is_starvation_only_on_an_empty_stomach(
    tmp_path: Path,
) -> None:
    ticks = tmp_path / "ticks.jsonl.gz"
    write_gz_jsonl(
        ticks,
        [
            tick_record(
                1,
                entity_updates=[
                    _update("ada", "player", food=0),
                    _update("bram", "player", food=40),
                ],
                deaths=[
                    {"entity_id": "ada", "killer_id": ""},
                    {"entity_id": "bram", "killer_id": ""},
                ],
            )
        ],
    )

    facts, moments, _conversations, _giving = analyze_run.scan_world_ticks(ticks)

    assert facts.deaths == {"ada": 1, "bram": 1}
    assert facts.killers == {"starvation": 1, "unknown": 1}
    texts = sorted(m.text for m in moments if m.kind == "death")
    assert texts == ["ada killed by starvation", "bram killed by unknown"]


def test_a_wolf_is_recognised_from_its_spawn_record_without_entity_types(
    tmp_path: Path,
) -> None:
    ticks = tmp_path / "ticks.jsonl.gz"
    write_gz_jsonl(
        ticks,
        [
            tick_record(
                1,
                entities_spawned=[{"entity_id": "w7", "entity_type": "wolf"}],
                deaths=[{"entity_id": "w7", "killer_id": "dov"}],
            )
        ],
    )

    facts, _moments, _conversations, _giving = analyze_run.scan_world_ticks(ticks)

    assert facts.deaths == {}
    assert facts.wolf_kills == {"dov": 1}


def test_the_world_section_separates_deaths_from_wolf_kills(
    run_dir: Path, capsys
) -> None:
    analyze_run.main([str(run_dir)])
    printed = capsys.readouterr().out
    assert "deaths: 1 settlers by={'wolf': 1}" in printed
    assert "wolf kills: 0 by={}" in printed
