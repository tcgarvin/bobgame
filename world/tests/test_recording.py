"""Tests for run recording (docs/07_replay.md)."""

import gzip
import json
import re
from pathlib import Path
from typing import Any

import pytest

from world.events import ObjectChange
from world.movement import MoveResult
from world.recording import (
    JsonlGzWriter,
    RunRecorder,
    generate_run_id,
    read_jsonl_gz,
)
from world.state import Entity, World, WorldObject
from world.tick import TickConfig, TickResult
from world.types import Position


@pytest.fixture
def world() -> World:
    world = World(width=20, height=20)
    world.add_entity(Entity(entity_id="alice", position=Position(x=2, y=2)))
    world.add_object(
        WorldObject(
            object_id="bush_1",
            position=Position(x=3, y=3),
            object_type="bush",
            state=(("berry_count", "1"),),
        )
    )
    return world


@pytest.fixture
def recorder(tmp_path: Path, world: World) -> RunRecorder:
    return RunRecorder(
        run_dir=tmp_path / "20260917-120000-foraging",
        run_id="20260917-120000-foraging",
        config_name="hamlet",
        config_path="world/configs/hamlet.toml",
        world=world,
        tick_config=TickConfig(tick_duration_ms=200, intent_deadline_ms=100),
    )


def read_meta(recorder: RunRecorder) -> dict[str, Any]:
    with open(recorder.meta_path, encoding="utf-8") as handle:
        meta: dict[str, Any] = json.load(handle)
    return meta


class TestJsonlGzWriter:
    def test_round_trips_records(self, tmp_path: Path) -> None:
        path = tmp_path / "lines.jsonl.gz"
        writer = JsonlGzWriter(path)
        writer.write({"a": 1})
        writer.write({"b": [1, 2, 3]})
        writer.close()

        assert list(read_jsonl_gz(path)) == [{"a": 1}, {"b": [1, 2, 3]}]

    def test_flush_makes_records_readable_before_close(self, tmp_path: Path) -> None:
        path = tmp_path / "lines.jsonl.gz"
        writer = JsonlGzWriter(path, flush_interval_s=0.0)
        writer.write({"a": 1})
        try:
            assert list(read_jsonl_gz(path)) == [{"a": 1}]
        finally:
            writer.close()

    def test_reader_tolerates_a_truncated_tail(self, tmp_path: Path) -> None:
        path = tmp_path / "lines.jsonl.gz"
        writer = JsonlGzWriter(path, flush_interval_s=0.0)
        for index in range(5):
            writer.write({"i": index})
        writer.close()

        raw = path.read_bytes()
        truncated = tmp_path / "truncated.jsonl.gz"
        truncated.write_bytes(raw[: len(raw) - 12])

        records = list(read_jsonl_gz(truncated))
        assert records  # some records survive
        assert records == [{"i": index} for index in range(len(records))]
        assert len(records) < 5 or records[-1] == {"i": 4}

    def test_writer_appends_to_one_gzip_member(self, tmp_path: Path) -> None:
        path = tmp_path / "lines.jsonl.gz"
        writer = JsonlGzWriter(path, flush_interval_s=0.0)
        writer.write({"a": 1})
        writer.write({"a": 2})
        writer.close()

        with gzip.open(path, "rb") as stream:
            assert stream.read().count(b"\n") == 2
        # A single member: exactly one gzip magic header at the start.
        assert path.read_bytes().count(b"\x1f\x8b\x08") == 1


class TestRunId:
    def test_format(self) -> None:
        run_id = generate_run_id("settlement")
        assert re.fullmatch(r"\d{8}-\d{6}-settlement", run_id)


class TestRunRecorder:
    def test_start_writes_meta_and_objects(self, recorder: RunRecorder) -> None:
        recorder.start()
        try:
            meta = read_meta(recorder)
            assert meta["format_version"] == 1
            assert meta["run_id"] == "20260917-120000-foraging"
            assert meta["config_name"] == "hamlet"
            assert meta["world_size"] == {"width": 20, "height": 20}
            assert meta["tick_duration_ms"] == 200
            assert meta["intent_deadline_ms"] == 100
            assert meta["map_path"] is None
            assert meta["map_sha256"] is None
            assert meta["finished_at"] is None
            assert meta["last_tick"] is None
            assert meta["entities"] == [
                {"entity_id": "alice", "entity_type": "default"}
            ]

            objects = list(read_jsonl_gz(recorder.world_dir / "objects.jsonl.gz"))
            assert objects == [
                {
                    "object_id": "bush_1",
                    "position": {"x": 3, "y": 3},
                    "object_type": "bush",
                    "state": {"berry_count": "1"},
                }
            ]
        finally:
            recorder.close()

    def test_records_ticks_and_agent_status(self, recorder: RunRecorder) -> None:
        recorder.start()
        recorder.world.advance_tick()
        recorder.record_tick(
            TickResult(
                tick_id=1,
                move_results=[
                    MoveResult(
                        entity_id="alice",
                        success=True,
                        from_pos=Position(x=2, y=2),
                        to_pos=Position(x=3, y=2),
                    )
                ],
                object_changes=[
                    ObjectChange(
                        object_id="bush_1",
                        field="berry_count",
                        old_value="1",
                        new_value="0",
                    )
                ],
                duration_ms=1.5,
            )
        )
        recorder.record_agent_status(
            {
                "type": "agent_status",
                "entity_id": "alice",
                "mode": "stint",
                "brief": "gather",
                "planner_thought": "berries",
                "stint": {"tick": 1},
            }
        )
        recorder.close()

        records = list(read_jsonl_gz(recorder.world_dir / "ticks.jsonl.gz"))
        assert [record["type"] for record in records] == ["tick", "agent_status"]

        tick = records[0]
        assert tick["tick_id"] == 1
        assert tick["moves"][0]["to"] == {"x": 3, "y": 2}
        assert tick["object_changes"][0]["new_value"] == "0"
        assert [update["entity_id"] for update in tick["entity_updates"]] == ["alice"]
        assert tick["wall_ms"] > 0
        for key in (
            "damage",
            "deaths",
            "respawns",
            "entities_spawned",
            "entities_despawned",
            "objects_added",
            "objects_removed",
            "actions",
            "utterances",
        ):
            assert key in tick

        status = records[1]
        assert status["tick_id"] == 1
        assert status["entity_id"] == "alice"
        assert status["stint"] == {"tick": 1}

        meta = read_meta(recorder)
        assert meta["finished_at"]
        assert meta["last_tick"] == 1

    def test_map_sha256_is_recorded(self, tmp_path: Path, world: World) -> None:
        map_file = tmp_path / "saves" / "island.npz"
        map_file.parent.mkdir()
        map_file.write_bytes(b"not really a map")

        recorder = RunRecorder(
            run_dir=tmp_path / "run",
            run_id="20260917-120000-island",
            config_name="hamlet",
            config_path="world/configs/hamlet.toml",
            world=world,
            tick_config=TickConfig(),
            map_path="saves/island.npz",
            project_root=tmp_path,
        )
        recorder.start()
        recorder.close()

        meta = read_meta(recorder)
        assert meta["map_path"] == "saves/island.npz"
        assert len(meta["map_sha256"]) == 64

    def test_write_error_disables_recording(self, recorder: RunRecorder) -> None:
        recorder.start()

        def fail(_record: dict[str, Any]) -> None:
            raise OSError("disk full")

        assert recorder._ticks is not None
        recorder._ticks.write = fail  # type: ignore[method-assign]
        recorder.record_tick(TickResult(tick_id=1, move_results=[]))

        assert recorder.enabled is False
        # Further records are dropped rather than raising.
        recorder.record_tick(TickResult(tick_id=2, move_results=[]))
        recorder.close()
