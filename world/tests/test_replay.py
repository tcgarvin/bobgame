"""Tests for the replay session and the replay WebSocket service."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import websockets

from world.events import (
    ActionResult,
    DeathEvent,
    EntityDespawnedEvent,
    ObjectAddedEvent,
    ObjectChange,
    ObjectRemovedEvent,
    UtteranceEvent,
)
from world.movement import MoveResult
from world.recording import RunRecorder
from world.replay.loader import RunLoader, RunLoadError
from world.replay.service import ReplayWebSocketService
from world.replay.session import ReplaySession
from world.state import Entity, World, WorldObject
from world.tick import TickConfig, TickResult
from world.types import Position

RUN_ID = "20260917-120000-testrun"


def build_run(runs_dir: Path) -> Path:
    """Write a small synthetic run: 20x20 world, six ticks, one despawn.

    Timeline:
      0  nothing
      1  alice moves (2,2) -> (3,2), alice reports an agent status
      2  bush_1 loses its berry (object change + collect action)
      3  item_pile_1 appears at (6,6)
      4  item_pile_1 is picked up (removed), bob says something
      5  wolf_1 dies and despawns
    """
    run_dir = runs_dir / RUN_ID
    world = World(width=20, height=20)
    world.settlement = Position(x=10, y=10)
    world.add_entity(Entity(entity_id="alice", position=Position(x=2, y=2)))
    world.add_entity(Entity(entity_id="bob", position=Position(x=5, y=5)))
    world.add_entity(
        Entity(entity_id="wolf_1", position=Position(x=8, y=8), entity_type="wolf")
    )
    world.add_object(
        WorldObject(
            object_id="bush_1",
            position=Position(x=3, y=3),
            object_type="bush",
            state=(("berry_count", "1"),),
        )
    )
    world.add_object(
        WorldObject(
            object_id="chest_1", position=Position(x=4, y=4), object_type="chest"
        )
    )

    recorder = RunRecorder(
        run_dir=run_dir,
        run_id=RUN_ID,
        config_name="testrun",
        config_path="world/configs/testrun.toml",
        world=world,
        tick_config=TickConfig(tick_duration_ms=100, intent_deadline_ms=50),
    )
    recorder.start()

    # Tick 0: nothing happened.
    recorder.record_tick(TickResult(tick_id=0, move_results=[]))

    # Tick 1: alice moves and reports her status.
    world.advance_tick()
    world.update_entity_position("alice", Position(x=3, y=2))
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
        )
    )
    recorder.record_agent_status(
        {
            "type": "agent_status",
            "entity_id": "alice",
            "mode": "stint",
            "brief": "pick berries",
            "planner_thought": "hungry",
            "stint": {"tick": 1, "action": "collect:bush_1"},
            "cost": {"total_usd": 0.0074, "jev_usd": 0.0025},
        }
    )

    # Tick 2: the bush loses its berry.
    world.advance_tick()
    bush = world.get_object("bush_1")
    world.update_object(bush.with_state("berry_count", "0"))
    recorder.record_tick(
        TickResult(
            tick_id=2,
            move_results=[],
            object_changes=[
                ObjectChange(
                    object_id="bush_1",
                    field="berry_count",
                    old_value="1",
                    new_value="0",
                )
            ],
            action_results=[
                ActionResult(
                    entity_id="alice",
                    action_type="collect",
                    success=True,
                    details="berry from bush_1",
                )
            ],
        )
    )

    # Tick 3: an item pile appears.
    world.advance_tick()
    pile = WorldObject(
        object_id="item_pile_1",
        position=Position(x=6, y=6),
        object_type="item_pile",
        state=(("contents", '{"berry": 1}'),),
    )
    world.add_object(pile)
    recorder.record_tick(
        TickResult(
            tick_id=3, move_results=[], objects_added=[ObjectAddedEvent(obj=pile)]
        )
    )

    # Tick 4: the pile is picked up and bob says something.
    world.advance_tick()
    world.remove_object("item_pile_1")
    recorder.record_tick(
        TickResult(
            tick_id=4,
            move_results=[],
            objects_removed=[
                ObjectRemovedEvent(object_id="item_pile_1", position=Position(x=6, y=6))
            ],
            utterances=[
                UtteranceEvent(
                    speaker_id="bob",
                    channel="local",
                    text="found berries",
                    position=Position(x=5, y=5),
                )
            ],
        )
    )

    # Tick 5: the wolf dies and is detached from the grid.
    world.advance_tick()
    wolf = world.get_entity("wolf_1")
    world.set_entity(wolf.as_dead())
    world.detach_entity("wolf_1")
    recorder.record_tick(
        TickResult(
            tick_id=5,
            move_results=[],
            deaths=[
                DeathEvent(
                    entity_id="wolf_1", killer_id="bob", position=Position(x=8, y=8)
                )
            ],
            entities_despawned=[
                EntityDespawnedEvent(
                    entity_id="wolf_1", position=Position(x=8, y=8), reason="killed"
                )
            ],
        )
    )
    recorder.close()
    return run_dir


@pytest.fixture
def runs_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "runs"
    directory.mkdir()
    build_run(directory)
    return directory


@pytest.fixture
def session(runs_dir: Path, tmp_path: Path) -> ReplaySession:
    return ReplaySession(RunLoader(runs_dir / RUN_ID), tmp_path)


class TestRunLoader:
    def test_loads_meta_ticks_and_objects(self, runs_dir: Path) -> None:
        loader = RunLoader(runs_dir / RUN_ID)

        assert loader.first_tick == 0
        assert loader.last_tick == 5
        assert loader.tick_duration_ms == 100
        assert {obj["object_id"] for obj in loader.iter_objects()} == {
            "bush_1",
            "chest_1",
        }
        assert loader.agent_ids() == ["alice"]

    def test_missing_ticks_file_raises(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "empty-run"
        (run_dir / "world").mkdir(parents=True)
        (run_dir / "meta.json").write_text("{}", encoding="utf-8")

        with pytest.raises(RunLoadError):
            RunLoader(run_dir)

    def test_run_index_lists_notable_moments(self, runs_dir: Path) -> None:
        index = RunLoader(runs_dir / RUN_ID).run_index()

        kinds = {(event["kind"], event["tick_id"]) for event in index["events"]}
        assert ("death", 5) in kinds
        assert ("wolf_killed", 5) in kinds
        assert ("say", 4) in kinds
        assert index["run_id"] == RUN_ID


class TestReplaySession:
    def test_starts_at_the_first_tick(self, session: ReplaySession) -> None:
        assert session.tick_id == 0
        assert session.world.get_object("bush_1").get_state("berry_count") == "1"
        assert session.world.get_entity("alice").position == Position(x=2, y=2)

    def test_seek_forward_applies_moves_and_object_deltas(
        self, session: ReplaySession
    ) -> None:
        session.seek(3)

        assert session.world.get_entity("alice").position == Position(x=3, y=2)
        assert session.world.get_object("bush_1").get_state("berry_count") == "0"
        assert "item_pile_1" in session.world.all_objects()

    def test_seek_past_a_removal_drops_the_object(self, session: ReplaySession) -> None:
        session.seek(4)
        assert "item_pile_1" not in session.world.all_objects()

    def test_seek_backward_restores_touched_objects(
        self, session: ReplaySession
    ) -> None:
        session.seek(4)
        session.seek(3)
        assert "item_pile_1" in session.world.all_objects()

        session.seek(0)
        assert "item_pile_1" not in session.world.all_objects()
        assert session.world.get_object("bush_1").get_state("berry_count") == "1"
        assert session.world.get_entity("alice").position == Position(x=2, y=2)
        assert session.world.object_count() == 2

    def test_dead_entities_stay_but_are_detached(self, session: ReplaySession) -> None:
        session.seek(5)

        wolf = session.world.get_entity("wolf_1")
        assert wolf.alive is False
        assert session.world.get_entity_at(Position(x=8, y=8)) is None

        session.seek(0)
        assert session.world.get_entity("wolf_1").alive is True
        assert session.world.get_entity_at(Position(x=8, y=8)) is not None

    def test_step_clamps_to_the_run_range(self, session: ReplaySession) -> None:
        assert session.step(-5) == 0
        assert session.seek(99) == 5
        assert session.at_end() is True

    def test_entity_log_collects_actions_and_utterances(
        self, session: ReplaySession
    ) -> None:
        session.seek(5)
        log = session.entity_log(5)

        kinds = {(entry["entity_id"], entry["kind"]) for entry in log["entries"]}
        assert ("alice", "action") in kinds
        assert ("bob", "utterance") in kinds

    def test_chunk_index_follows_entities(self, session: ReplaySession) -> None:
        session.seek(1)
        chunk = session.chunk_manager.get_chunk(0, 0)
        assert chunk is not None
        assert "alice" in chunk.entities


# --- WebSocket service -----------------------------------------------------


class ReplayTestClient:
    """A websocket client that collects messages in order."""

    def __init__(self, connection: Any):
        self.connection = connection

    async def send(self, message: dict[str, Any]) -> None:
        await self.connection.send(json.dumps(message))

    async def collect_until(
        self, message_type: str, timeout: float = 5.0
    ) -> list[dict[str, Any]]:
        """Read messages until one of `message_type` arrives (inclusive)."""
        messages: list[dict[str, Any]] = []
        while True:
            raw = await asyncio.wait_for(self.connection.recv(), timeout=timeout)
            message = json.loads(raw)
            messages.append(message)
            if message["type"] in (message_type, "error"):
                return messages


@pytest_asyncio.fixture
async def replay_client(runs_dir: Path, tmp_path: Path) -> Any:
    service = ReplayWebSocketService(
        runs_dir=runs_dir, project_root=tmp_path, host="127.0.0.1", port=0
    )
    await service.start()
    assert service._server is not None
    port = service._server.sockets[0].getsockname()[1]
    async with websockets.connect(f"ws://127.0.0.1:{port}") as connection:
        yield ReplayTestClient(connection)
    await service.stop()


@pytest.mark.asyncio
class TestReplayWebSocketService:
    async def test_open_run_message_order(self, replay_client: Any) -> None:
        await replay_client.send({"type": "open_run", "run_id": RUN_ID})
        messages = await replay_client.collect_until("replay_status")

        # No agent status was recorded at tick 0, so none is replayed there.
        assert [message["type"] for message in messages] == [
            "snapshot",
            "run_index",
            "tick_completed",
            "entity_log",
            "replay_status",
        ]
        snapshot = messages[0]
        assert snapshot["run_id"] == RUN_ID
        assert snapshot["replay"] == {
            "run_id": RUN_ID,
            "first_tick": 0,
            "last_tick": 5,
            "tick_id": 0,
            "playing": False,
            "speed": 1.0,
        }
        assert snapshot["settlement"] == {"x": 10, "y": 10}

    async def test_seek_replays_agent_status(self, replay_client: Any) -> None:
        await replay_client.send({"type": "open_run", "run_id": RUN_ID})
        await replay_client.collect_until("replay_status")

        await replay_client.send({"type": "seek", "tick_id": 3})
        messages = await replay_client.collect_until("replay_status")
        types = [message["type"] for message in messages]
        assert types == [
            "snapshot",
            "tick_completed",
            "entity_log",
            "agent_status",
            "replay_status",
        ]
        status = messages[types.index("agent_status")]
        assert status["entity_id"] == "alice"
        assert status["brief"] == "pick berries"
        assert status["stint"] == {"tick": 1, "action": "collect:bush_1"}
        assert status["cost"] == {"total_usd": 0.0074, "jev_usd": 0.0025}

    async def test_unknown_run_replies_with_error(self, replay_client: Any) -> None:
        await replay_client.send({"type": "open_run", "run_id": "nope"})
        messages = await replay_client.collect_until("error")

        assert messages[-1]["type"] == "error"
        assert "nope" in messages[-1]["message"]

    async def test_subscribe_viewport_then_seek_resends_chunks(
        self, replay_client: Any
    ) -> None:
        await replay_client.send({"type": "open_run", "run_id": RUN_ID})
        await replay_client.collect_until("replay_status")

        await replay_client.send(
            {
                "type": "subscribe_viewport",
                "viewport": {"x": 0, "y": 0, "width": 20, "height": 20},
            }
        )
        chunk_messages = await replay_client.collect_until("chunk_data")
        assert chunk_messages[-1]["type"] == "chunk_data"

        await replay_client.send({"type": "seek", "tick_id": 3})
        messages = await replay_client.collect_until("replay_status")
        types = [message["type"] for message in messages]
        assert types[0] == "snapshot"
        assert "chunk_data" in types
        assert types.index("chunk_data") < types.index("tick_completed")
        assert types[-1] == "replay_status"
        assert messages[-1]["tick_id"] == 3

        tick_completed = next(m for m in messages if m["type"] == "tick_completed")
        assert any(
            obj["object_id"] == "item_pile_1" for obj in tick_completed["objects_added"]
        )

    async def test_step_moves_backwards(self, replay_client: Any) -> None:
        await replay_client.send({"type": "open_run", "run_id": RUN_ID})
        await replay_client.collect_until("replay_status")
        await replay_client.send({"type": "seek", "tick_id": 4})
        await replay_client.collect_until("replay_status")

        await replay_client.send({"type": "step", "delta": -2})
        messages = await replay_client.collect_until("replay_status")
        assert messages[-1]["tick_id"] == 2
        assert messages[-1]["playing"] is False

    async def test_play_reaches_last_tick_then_pauses(self, replay_client: Any) -> None:
        await replay_client.send({"type": "open_run", "run_id": RUN_ID})
        await replay_client.collect_until("replay_status")

        await replay_client.send({"type": "play", "speed": 10})
        statuses: list[dict[str, Any]] = []
        while True:
            messages = await replay_client.collect_until("replay_status")
            statuses.append(messages[-1])
            if statuses[-1]["playing"] is False:
                break

        assert statuses[-1]["tick_id"] == 5
        assert statuses[0]["playing"] is True

    async def test_get_agent_detail_with_and_without_traces(
        self, replay_client: Any
    ) -> None:
        await replay_client.send({"type": "open_run", "run_id": RUN_ID})
        await replay_client.collect_until("replay_status")

        await replay_client.send(
            {"type": "get_agent_detail", "entity_id": "alice", "tick_id": 2}
        )
        messages = await replay_client.collect_until("agent_detail")
        detail = messages[-1]
        assert detail["entity_id"] == "alice"
        assert detail["tick_id"] == 2
        # No agent trace files in this run: every slot is empty but present.
        assert detail["stint"] is None
        assert detail["record"] is None
        assert detail["jev_state"] is None
        assert detail["planner_turn"] is None
        assert detail["memory"] == ""

    async def test_get_run_index(self, replay_client: Any) -> None:
        await replay_client.send({"type": "open_run", "run_id": RUN_ID})
        await replay_client.collect_until("replay_status")

        await replay_client.send({"type": "get_run_index"})
        messages = await replay_client.collect_until("run_index")
        index = messages[-1]
        assert index["agents"] == ["alice"]
        assert any(event["kind"] == "death" for event in index["events"])

    async def test_unknown_message_type_errors(self, replay_client: Any) -> None:
        await replay_client.send({"type": "nonsense"})
        messages = await replay_client.collect_until("error")
        assert "nonsense" in messages[-1]["message"]
