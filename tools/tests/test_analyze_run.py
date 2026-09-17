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
                    {"entity_id": "wolf_1", "entity_type": "wolf",
                     "position": {"x": 5, "y": 5}}
                ],
            ),
            tick_record(
                3,
                actions=[
                    {"entity_id": "ada", "action_type": "craft",
                     "success": True, "details": "crafted sword"},
                    {"entity_id": "ada", "action_type": "craft",
                     "success": False, "details": "unknown recipe chest"},
                    {"entity_id": "bram", "action_type": "place",
                     "success": True, "details": "placed chest_3 at (6, 6)"},
                    {"entity_id": "bram", "action_type": "write_note",
                     "success": True, "details": "wolves to the north"},
                ],
            ),
            tick_record(
                4,
                damage=[
                    {"entity_id": "wolf_1", "attacker_id": "ada",
                     "amount": 4, "remaining_health": 0}
                ],
                entities_despawned=[
                    {"entity_id": "wolf_1", "reason": "killed",
                     "position": {"x": 5, "y": 5}}
                ],
            ),
            tick_record(
                5,
                deaths=[
                    {"entity_id": "bram", "killer_id": "wolf_2",
                     "position": {"x": 6, "y": 6}}
                ],
            ),
        ],
    )

    agents = root / "agents"
    write_gz_jsonl(
        agents / "agent-ada" / "stints.jsonl.gz",
        [
            {"event": "stint_start", "entity_id": "ada", "tick": 1,
             "stint_id": "ada-1", "brief": {"instruction": "gather wood"}},
            {"entity_id": "ada", "tick": 2, "stint_id": "ada-1",
             "action": "extract:tree_1", "top": [["extract:tree_1", 0.8]],
             "probabilities": {"extract:tree_1": 0.8, "wait": 0.2},
             "confidence": 0.8, "eject": 0.1, "danger": 0.2,
             "latency_ms": 200, "input_tokens": 2000,
             "intent_result": "accepted"},
            {"entity_id": "ada", "tick": 3, "stint_id": "ada-1",
             "action": "craft:sword", "top": [["craft:sword", 0.6]],
             "confidence": 0.6, "eject": 0.3, "danger": 0.1,
             "latency_ms": 400, "input_tokens": 2100,
             "intent_result": "rejected"},
            {"event": "stint_end", "entity_id": "ada", "tick": 4,
             "stint_id": "ada-1", "end_reason": "eject", "ticks_used": 3,
             "max_ticks": 20, "brief": {}, "report": "done"},
        ],
    )
    write_gz_jsonl(
        agents / "agent-ada" / "planner.jsonl.gz",
        [
            {"event": "turn_start", "entity_id": "ada", "tick": 1, "turn": 1,
             "prompt": "…"},
            {"event": "tool_call", "entity_id": "ada", "tick": 1, "turn": 1,
             "tool": "look", "args": {}},
            {"event": "turn_end", "entity_id": "ada", "tick": 4, "turn": 1,
             "thought": "Gathered wood.", "tool_calls": 1, "duration_ms": 900},
            {"event": "turn_failed", "entity_id": "ada", "tick": 5, "turn": 2,
             "error": "model returned no tool call"},
            {"event": "history_reset", "entity_id": "ada", "tick": 5,
             "turn": 2},
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
    assert world["killers"] == {"wolf_2": 1}
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
        "first_wolf", "craft", "place", "write_note", "wolf_killed",
        "death", "planner_failed", "history_reset",
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
            {"entity_id": "bob", "tick": 1, "action": "wait",
             "top": [["wait", 0.9]], "eject": 0.1, "danger": 0.0,
             "latency_ms": 100, "input_tokens": 500,
             "intent_result": "accepted"}
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
    return {"entity_id": entity, "action_type": action_type,
            "success": True, "details": details}


def test_building_actions_are_counted_and_only_firsts_become_moments(
    tmp_path: Path,
) -> None:
    ticks = tmp_path / "ticks.jsonl.gz"
    write_gz_jsonl(
        ticks,
        [
            tick_record(1, actions=[
                _action("ada", "craft", "crafted plank x2"),
                _action("ada", "craft", "crafted workshop_table"),
                _action("bram", "craft", "crafted bed"),
            ]),
            tick_record(2, actions=[
                _action("ada", "place", "placed wood_wall_7 at (3, 4)"),
                _action("bram", "place", "placed wood_wall_8 at (3, 5)"),
                _action("bram", "place", "placed bed_9 at (4, 5)"),
            ]),
            tick_record(3, actions=[
                _action("bram", "rest", "rested at bed_9 (+2 health)"),
                _action("ada", "extract", "dismantling wood_wall_7 (1/3)"),
                _action("ada", "extract", "dismantled wood_wall_7 (+1 wood_wall)"),
                _action("ada", "extract", "extracted wood from tree_1"),
            ]),
        ],
    )

    facts, moments = analyze_run.scan_world_ticks(ticks)

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
