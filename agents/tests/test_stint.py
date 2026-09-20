"""Stint mechanics: Jev's answer becomes an intent, and the code rules end it."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Iterator

import pytest

from agents.jev_agent.build import BUILD_OUT_OF_ITEMS, BuildExecutor, make_plan
from agents.jev_agent.jevclient import JevDecision
from agents.jev_agent.options import BriefHail, TravelState
from agents.jev_agent.pricing import jev_cost_usd
from agents.jev_agent.stint import (
    END_DEATH,
    END_LOST,
    LOST_EXPLANATION,
    END_REPEATED_FAILURE,
    END_SUCCESS_OR_JUDGEMENT,
    END_TICKS,
    Brief,
    Stint,
)
from agents.jev_agent.tracelog import AgentTrace
from agents import world_pb2 as pb
from agents.jev_agent.worldmodel import WorldModel

from helpers import (
    FakeJevClient,
    acted_event,
    make_entity,
    make_object,
    make_observation,
)


def decision(
    action: str,
    *,
    done: float = 0.0,
    stuck: float = 0.0,
    lost: float = 0.0,
    danger: float = 0.0,
) -> JevDecision:
    """A scripted Jev answer."""
    return JevDecision(
        action=action,
        probabilities={action: 0.8, "wait": 0.2},
        confidence=0.7,
        lost=lost,
        done=done,
        stuck=stuck,
        danger=danger,
        input_tokens=420,
        latency_ms=250,
    )


def make_brief(**overrides: object) -> Brief:
    """A default brief, with overrides."""
    fields: dict[str, object] = {
        "instruction": "Chop the nearest tree",
        "success_condition": "you hold 2 more wood",
        "max_ticks": 5,
    }
    fields.update(overrides)
    return Brief(**fields)  # type: ignore[arg-type]


class StintHarness:
    """Drives a stint over a scripted sequence of observations."""

    def __init__(
        self,
        jev: FakeJevClient,
        brief: Brief,
        trace: AgentTrace,
        driver: object | None = None,
    ) -> None:
        self.model = WorldModel("ada")
        self.jev = jev
        self.trace = trace
        self.stint = Stint(brief, self.model, jev, trace=trace, driver=driver)  # type: ignore[arg-type]

    async def tick(self, observation) -> object:  # type: ignore[no-untyped-def]
        """Feed one observation through the model and the stint."""
        digest = self.model.update(observation)
        intent = await self.stint.decide(digest)
        self.stint.record_intent_result("accepted")
        return intent


@pytest.fixture
def trace(tmp_path: Path) -> Iterator[AgentTrace]:
    """A throwaway trace directory for `ada`."""
    agent_trace = AgentTrace("ada", tmp_path / "logs")
    yield agent_trace
    agent_trace.close()


def read_lines(path: Path) -> list[dict[str, object]]:
    """Every JSON record in a gzip JSONL file."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


async def test_jev_choice_becomes_the_matching_intent(trace: AgentTrace) -> None:
    jev = FakeJevClient(script=[decision("move_E")])
    harness = StintHarness(jev, make_brief(), trace)
    intent = await harness.tick(make_observation(1, make_entity("ada", (10, 10))))
    assert intent.HasField("move")
    assert intent.move.direction == 3  # EAST


async def test_extract_choice_targets_the_right_object(trace: AgentTrace) -> None:
    jev = FakeJevClient(script=[decision("extract:tree_1")])
    harness = StintHarness(jev, make_brief(), trace)
    intent = await harness.tick(
        make_observation(
            1,
            make_entity("ada", (10, 10)),
            objects=[make_object("tree_1", "tree", (11, 10))],
        )
    )
    assert intent.extract.object_id == "tree_1"


async def test_an_option_jev_never_saw_falls_back_to_wait(trace: AgentTrace) -> None:
    jev = FakeJevClient(script=[decision("fly_to_the_moon")])
    harness = StintHarness(jev, make_brief(), trace)
    intent = await harness.tick(make_observation(1, make_entity("ada", (10, 10))))
    assert intent.HasField("wait")
    assert "unknown option" in harness.stint.records[0].note


async def test_two_high_done_ticks_in_a_row_end_the_stint(trace: AgentTrace) -> None:
    jev = FakeJevClient(
        script=[
            decision("wait", done=0.9),
            decision("wait", done=0.85),
            decision("wait"),
        ]
    )
    harness = StintHarness(jev, make_brief(), trace)
    await harness.tick(make_observation(1, make_entity("ada", (10, 10))))
    assert not harness.stint.finished
    await harness.tick(make_observation(2, make_entity("ada", (10, 10))))
    assert not harness.stint.finished, "the stint ends on the tick after the streak"
    await harness.tick(make_observation(3, make_entity("ada", (10, 10))))
    assert harness.stint.finished
    assert harness.stint.end_reason == END_SUCCESS_OR_JUDGEMENT


async def test_two_high_stuck_ticks_in_a_row_end_the_stint(trace: AgentTrace) -> None:
    jev = FakeJevClient(
        script=[
            decision("wait", stuck=0.6),
            decision("wait", stuck=0.95),
            decision("wait"),
        ]
    )
    harness = StintHarness(jev, make_brief(max_ticks=10), trace)
    for tick in range(1, 4):
        await harness.tick(make_observation(tick, make_entity("ada", (10, 10))))
    assert harness.stint.finished
    assert harness.stint.end_reason == END_SUCCESS_OR_JUDGEMENT


async def test_answers_below_the_threshold_never_end_the_stint(
    trace: AgentTrace,
) -> None:
    jev = FakeJevClient(
        script=[decision("wait", done=0.55, stuck=0.55) for _ in range(4)]
    )
    harness = StintHarness(jev, make_brief(max_ticks=10), trace)
    for tick in range(1, 5):
        await harness.tick(make_observation(tick, make_entity("ada", (10, 10))))
    assert not harness.stint.finished


async def test_the_report_tail_shows_done_and_stuck(trace: AgentTrace) -> None:
    jev = FakeJevClient(script=[decision("wait", done=0.42, stuck=0.13, danger=0.03)])
    harness = StintHarness(jev, make_brief(max_ticks=10), trace)
    await harness.tick(make_observation(1, make_entity("ada", (10, 10))))
    harness.stint.record_intent_result("accepted")
    tail = harness.stint.build_report().tail
    assert tail == [
        "t1 wait -> accepted (done 0.42, stuck 0.13, lost 0.00, danger 0.03)"
    ]


async def test_two_high_lost_ticks_end_the_stint_as_lost(trace: AgentTrace) -> None:
    jev = FakeJevClient(
        script=[
            decision("move_N", lost=0.7),
            decision("move_S", lost=0.8),
            decision("wait"),
        ]
    )
    harness = StintHarness(jev, make_brief(max_ticks=10), trace)
    for tick in range(1, 4):
        await harness.tick(make_observation(tick, make_entity("ada", (10, 10))))
    assert harness.stint.finished
    assert harness.stint.end_reason == END_LOST
    text = harness.stint.build_report().to_text()
    assert "ended because: lost" in text
    assert LOST_EXPLANATION in text


async def test_a_high_lost_tick_between_low_ones_does_not_end_it(
    trace: AgentTrace,
) -> None:
    jev = FakeJevClient(
        script=[decision("wait", lost=0.9), decision("wait", lost=0.1)] * 2
    )
    harness = StintHarness(jev, make_brief(), trace)
    for tick in range(1, 5):
        await harness.tick(make_observation(tick, make_entity("ada", (10, 10))))
    assert not harness.stint.finished


async def test_a_single_high_done_does_not_end_the_stint(trace: AgentTrace) -> None:
    jev = FakeJevClient(
        script=[decision("wait", done=0.9), decision("wait", done=0.1)] * 2
    )
    harness = StintHarness(jev, make_brief(), trace)
    for tick in range(1, 5):
        await harness.tick(make_observation(tick, make_entity("ada", (10, 10))))
    assert not harness.stint.finished


async def test_the_tick_budget_ends_the_stint(trace: AgentTrace) -> None:
    jev = FakeJevClient()
    harness = StintHarness(jev, make_brief(max_ticks=2), trace)
    await harness.tick(make_observation(1, make_entity("ada", (10, 10))))
    await harness.tick(make_observation(2, make_entity("ada", (10, 10))))
    assert not harness.stint.finished
    await harness.tick(make_observation(3, make_entity("ada", (10, 10))))
    assert harness.stint.finished
    assert harness.stint.end_reason == END_TICKS
    assert harness.stint.ticks_used == 2


async def test_death_ends_the_stint(trace: AgentTrace) -> None:
    jev = FakeJevClient()
    harness = StintHarness(jev, make_brief(), trace)
    await harness.tick(make_observation(1, make_entity("ada", (10, 10))))
    await harness.tick(
        make_observation(2, make_entity("ada", (10, 10), health=0, alive=False))
    )
    assert harness.stint.finished
    assert harness.stint.end_reason == END_DEATH


async def test_three_identical_failures_end_the_stint(trace: AgentTrace) -> None:
    jev = FakeJevClient(default_action="move_N")
    harness = StintHarness(jev, make_brief(max_ticks=20), trace)
    failure = acted_event("ada", "move", False, "blocked")
    await harness.tick(make_observation(1, make_entity("ada", (10, 10))))
    for tick in range(2, 5):
        await harness.tick(
            make_observation(tick, make_entity("ada", (10, 10)), events=[failure])
        )
    assert harness.stint.finished
    assert harness.stint.end_reason == END_REPEATED_FAILURE


async def test_a_success_resets_the_failure_streak(trace: AgentTrace) -> None:
    jev = FakeJevClient(default_action="move_N")
    harness = StintHarness(jev, make_brief(max_ticks=20), trace)
    failure = acted_event("ada", "move", False, "blocked")
    success = acted_event("ada", "move", True, "moved N")
    await harness.tick(make_observation(1, make_entity("ada", (10, 10))))
    await harness.tick(
        make_observation(2, make_entity("ada", (10, 10)), events=[failure])
    )
    await harness.tick(
        make_observation(3, make_entity("ada", (10, 10)), events=[failure])
    )
    await harness.tick(
        make_observation(4, make_entity("ada", (10, 9)), events=[success])
    )
    await harness.tick(
        make_observation(5, make_entity("ada", (10, 9)), events=[failure])
    )
    assert not harness.stint.finished


async def test_danger_rule_overrides_jev_when_death_is_close(trace: AgentTrace) -> None:
    jev = FakeJevClient(script=[decision("wait", danger=0.95)])
    harness = StintHarness(jev, make_brief(), trace)
    intent = await harness.tick(
        make_observation(
            1,
            make_entity("ada", (10, 10), health=4),
            entities=[make_entity("wolf_1", (11, 10), entity_type="wolf")],
        )
    )
    assert intent.HasField("move")
    assert harness.stint.records[0].note.startswith("danger override")


async def test_danger_rule_leaves_healthy_actors_alone(trace: AgentTrace) -> None:
    jev = FakeJevClient(script=[decision("wait", danger=0.95)])
    harness = StintHarness(jev, make_brief(), trace)
    intent = await harness.tick(
        make_observation(
            1,
            make_entity("ada", (10, 10), health=18),
            entities=[make_entity("wolf_1", (11, 10), entity_type="wolf")],
        )
    )
    assert intent.HasField("wait")


async def test_a_jev_failure_falls_back_to_wait_without_crashing(
    trace: AgentTrace,
) -> None:
    class BrokenJev:
        async def decide(self, state, options):  # type: ignore[no-untyped-def]
            raise RuntimeError("typesafe is down")

    model = WorldModel("ada")
    stint = Stint(make_brief(), model, BrokenJev(), trace=trace)
    digest = model.update(make_observation(1, make_entity("ada", (10, 10))))
    intent = await stint.decide(digest)
    stint.record_intent_result("accepted")
    assert intent.HasField("wait")
    assert "jev_error" in stint.records[0].note
    assert not stint.finished


async def test_check_every_repeats_the_last_action_between_calls(
    trace: AgentTrace,
) -> None:
    jev = FakeJevClient(default_action="move_E")
    harness = StintHarness(jev, make_brief(max_ticks=6, check_every=2), trace)
    for tick in range(1, 5):
        # The actor really moves east each tick, so no move counts as blocked.
        await harness.tick(make_observation(tick, make_entity("ada", (9 + tick, 10))))
    assert len(jev.calls) == 2, "Jev is asked every other tick"
    assert [record.action for record in harness.stint.records] == ["move_E"] * 4
    assert harness.stint.records[1].note == "repeat"


async def test_accepted_move_that_goes_nowhere_counts_as_blocked(
    trace: AgentTrace,
) -> None:
    jev = FakeJevClient(default_action="move_E")
    harness = StintHarness(jev, make_brief(max_ticks=20), trace)
    for tick in range(1, 5):
        await harness.tick(make_observation(tick, make_entity("ada", (10, 10))))
    results = [record.intent_result for record in harness.stint.records]
    assert results[:3] == ["blocked", "blocked", "blocked"]
    assert harness.stint.finished, "three blocked moves in a row end the stint"
    assert any("blocked" in line for line in harness.model.recent_history())


async def test_the_state_carries_what_the_stint_has_done_so_far(
    trace: AgentTrace,
) -> None:
    jev = FakeJevClient(default_action="move_E")
    harness = StintHarness(jev, make_brief(max_ticks=9), trace)
    for tick in range(1, 4):
        await harness.tick(
            make_observation(
                tick,
                make_entity("ada", (9 + tick, 10), inventory={"wood": tick}),
            )
        )
    so_far = jev.last_state["so_far"]
    assert so_far["ticks_used"] == 2
    assert so_far["ticks_left"] == 7
    assert so_far["actions"] == {"move": 2}
    assert so_far["inventory_change"] == {"wood": 2}
    assert so_far["moved_from_start"] == "dx 2 dy 0"
    assert so_far["net_tiles_moved"] == 2


async def test_travel_choice_sets_and_clears_the_travel(trace: AgentTrace) -> None:
    jev = FakeJevClient(
        script=[decision("step_towards:tree_1"), decision("stop_going")]
    )
    harness = StintHarness(jev, make_brief(), trace)
    observation = make_observation(
        1,
        make_entity("ada", (10, 10)),
        objects=[make_object("tree_1", "tree", (14, 10))],
    )
    await harness.tick(observation)
    assert harness.stint.travel is not None
    assert harness.stint.travel.label.startswith("tree_1")

    await harness.tick(make_observation(2, make_entity("ada", (11, 10))))
    assert harness.stint.travel is None


async def test_a_preset_travel_offers_keep_going_on_the_first_tick(
    trace: AgentTrace,
) -> None:
    jev = FakeJevClient(default_action="keep_going")
    brief = make_brief(travel=TravelState(target=(14, 10), label="(14, 10)"))
    harness = StintHarness(jev, brief, trace)
    intent = await harness.tick(make_observation(1, make_entity("ada", (10, 10))))
    assert "keep_going" in jev.last_options
    assert intent.move.direction == 3  # EAST


async def test_every_tick_is_written_to_the_stint_trace(trace: AgentTrace) -> None:
    jev = FakeJevClient(script=[decision("move_E"), decision("wait")])
    harness = StintHarness(jev, make_brief(), trace)
    await harness.tick(make_observation(1, make_entity("ada", (10, 10))))
    await harness.tick(make_observation(2, make_entity("ada", (11, 10))))
    # Rows are written one tick late, so the stint has to be over before the
    # trace is complete (see `_flush_finished_record`).
    harness.stint.finish("test_over")
    trace.close()

    lines = read_lines(trace.directory / "stints.jsonl.gz")
    start, first, second = lines[:3]
    assert start["event"] == "stint_start"
    assert start["stint_id"] == "ada-1"
    assert start["brief"] == {
        "instruction": "Chop the nearest tree",
        "success_condition": "you hold 2 more wood",
        "max_ticks": 5,
        "notes": "",
        "check_every": 1,
        "shouts": [],
        "hails": [],
        "places": {},
        "travel": None,
    }
    assert first["entity_id"] == "ada"
    assert first["stint_id"] == "ada-1"
    assert first["tick"] == 1
    assert first["action"] == "move_E"
    assert first["input_tokens"] == 420
    assert first["cost_usd"] == jev_cost_usd(420)
    assert first["latency_ms"] == 250
    assert first["intent_result"] == "accepted"
    assert first["options"] > 5
    assert first["top"][0][0] == "move_E"
    assert first["probabilities"] == {"move_E": 0.8, "wait": 0.2}
    assert first["confidence"] == 0.7
    assert second["tick"] == 2
    assert "event" not in first


async def test_a_silently_blocked_move_reaches_the_trace(trace: AgentTrace) -> None:
    """The row must not go to disk until the blocked-move check has seen it."""
    jev = FakeJevClient(default_action="move_E")
    harness = StintHarness(jev, make_brief(max_ticks=20), trace)
    for tick in range(1, 4):
        await harness.tick(make_observation(tick, make_entity("ada", (10, 10))))
    trace.close()

    rows = [
        line
        for line in read_lines(trace.directory / "stints.jsonl.gz")
        if "action" in line
    ]
    assert rows, "the first tick's row was written"
    assert rows[0]["intent_result"] == "blocked"


async def test_a_preset_travel_is_serialised_in_the_start_line(
    trace: AgentTrace,
) -> None:
    jev = FakeJevClient(default_action="keep_going")
    brief = make_brief(travel=TravelState(target=(14, 10), label="(14, 10)"))
    harness = StintHarness(jev, brief, trace)
    await harness.tick(make_observation(1, make_entity("ada", (10, 10))))
    trace.close()

    start = read_lines(trace.directory / "stints.jsonl.gz")[0]
    assert start["brief"]["travel"] == {"target": [14, 10], "label": "(14, 10)"}  # type: ignore[index]


async def test_the_end_line_carries_the_report(trace: AgentTrace) -> None:
    jev = FakeJevClient(default_action="wait")
    harness = StintHarness(jev, make_brief(max_ticks=1), trace)
    await harness.tick(make_observation(1, make_entity("ada", (10, 10))))
    await harness.tick(make_observation(2, make_entity("ada", (10, 10))))
    trace.close()

    end = read_lines(trace.directory / "stints.jsonl.gz")[-1]
    assert end["event"] == "stint_end"
    assert end["stint_id"] == "ada-1"
    assert end["end_reason"] == END_TICKS
    assert end["ticks_used"] == 1
    assert end["max_ticks"] == 1
    assert isinstance(end["report"], str)
    assert "STINT REPORT: Chop the nearest tree" in end["report"]


async def test_jev_states_are_logged_once_per_real_call(trace: AgentTrace) -> None:
    jev = FakeJevClient(default_action="move_E")
    harness = StintHarness(jev, make_brief(max_ticks=6, check_every=2), trace)
    for tick in range(1, 5):
        await harness.tick(make_observation(tick, make_entity("ada", (9 + tick, 10))))
    trace.close()

    states = read_lines(trace.directory / "jev_states.jsonl.gz")
    assert len(states) == len(jev.calls) == 2, "repeats do not call Jev or log a state"
    assert [state["tick"] for state in states] == [1, 3]
    assert states[0]["entity_id"] == "ada"
    assert states[0]["stint_id"] == "ada-1"
    assert states[0]["state"] == jev.calls[0][0]
    assert states[0]["criteria"] == jev.calls[0][1]


async def test_a_timed_out_call_still_logs_the_state_it_sent(
    trace: AgentTrace, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from agents.jev_agent import stint as stint_module

    class SlowJev(FakeJevClient):
        async def decide(self, state, options):  # type: ignore[no-untyped-def]
            if len(self.calls) >= 1:
                await asyncio.sleep(0.2)
            return await super().decide(state, options)

    monkeypatch.setattr(stint_module, "JEV_TICK_BUDGET_SECONDS", 0.05)
    jev = SlowJev(default_action="move_E")
    harness = StintHarness(jev, make_brief(max_ticks=10), trace)
    await harness.tick(make_observation(1, make_entity("ada", (10, 10))))
    await harness.tick(make_observation(2, make_entity("ada", (11, 10))))
    trace.close()

    states = read_lines(trace.directory / "jev_states.jsonl.gz")
    assert [state["tick"] for state in states] == [1, 2]


async def test_the_report_summarises_the_whole_stint(trace: AgentTrace) -> None:
    jev = FakeJevClient(default_action="move_E")
    harness = StintHarness(jev, make_brief(max_ticks=2), trace)
    await harness.tick(
        make_observation(1, make_entity("ada", (10, 10), inventory={"wood": 1}))
    )
    await harness.tick(
        make_observation(
            2,
            make_entity("ada", (11, 10), health=17, inventory={"wood": 3}),
            events=[
                acted_event("ada", "extract", True, "chopped tree_1 (+1 wood)"),
            ],
        )
    )
    await harness.tick(
        make_observation(3, make_entity("ada", (12, 10), inventory={"wood": 3}))
    )

    report = harness.stint.build_report()
    text = report.to_text()
    assert report.end_reason == END_TICKS
    assert report.ticks_used == 2
    assert report.start_position == (10, 10)
    assert report.end_position == (12, 10)
    assert report.inventory_delta == {"wood": 2}
    assert report.action_counts["move_E"] == (2, 0)
    assert "STINT REPORT: Chop the nearest tree" in text
    assert "ended because: ticks_exhausted" in text
    assert "wood +2" in text
    assert len(text.split("\n")) <= 22


async def test_the_report_mentions_damage_and_speech(trace: AgentTrace) -> None:
    jev = FakeJevClient()
    harness = StintHarness(jev, make_brief(max_ticks=4), trace)
    await harness.tick(make_observation(1, make_entity("ada", (10, 10))))
    await harness.tick(
        make_observation(
            2,
            make_entity("ada", (10, 10), health=17),
            events=[
                pb.ObservationEvent(
                    entity_damaged=pb.EntityDamaged(
                        entity_id="ada",
                        attacker_id="wolf_1",
                        amount=3,
                        remaining_health=17,
                    )
                ),
                pb.ObservationEvent(
                    utterance=pb.Utterance(
                        speaker_id="bob", channel="local", text="wolf!"
                    )
                ),
            ],
        )
    )
    report = harness.stint.build_report()
    assert any("3 damage from wolf_1" in line for line in report.notable)
    assert any("bob" in line for line in report.notable)


async def test_status_json_reports_the_last_decision(trace: AgentTrace) -> None:
    jev = FakeJevClient(script=[decision("move_E", done=0.4, danger=0.2)])
    harness = StintHarness(jev, make_brief(), trace)
    await harness.tick(make_observation(1, make_entity("ada", (10, 10))))
    payload = json.loads(harness.stint.status_json())
    assert payload["action"] == "move_E"
    assert payload["done"] == 0.4
    assert payload["stuck"] == 0.0
    assert payload["eject"] == 0.4
    assert payload["danger"] == 0.2
    assert payload["ticks_used"] == 1
    assert payload["stint_id"] == "ada-1"
    assert payload["success_condition"] == "you hold 2 more wood"
    assert payload["notes"] == ""


async def test_a_slow_jev_answer_falls_back_to_repeating_the_last_action(
    trace: AgentTrace, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from agents.jev_agent import stint as stint_module

    class SlowJev(FakeJevClient):
        async def decide(self, state, options):  # type: ignore[no-untyped-def]
            if len(self.calls) >= 1:
                await asyncio.sleep(0.2)
            return await super().decide(state, options)

    monkeypatch.setattr(stint_module, "JEV_TICK_BUDGET_SECONDS", 0.05)
    jev = SlowJev(default_action="move_E")
    harness = StintHarness(jev, make_brief(max_ticks=10), trace)
    await harness.tick(make_observation(1, make_entity("ada", (10, 10))))
    intent = await harness.tick(make_observation(2, make_entity("ada", (11, 10))))
    assert intent.HasField("move"), "the last action is repeated on timeout"
    assert harness.stint.records[-1].note == "jev_timeout"


# --- code drivers (the planner's build tool) --------------------------------


async def test_a_driver_replaces_jev_for_the_whole_stint(trace: AgentTrace) -> None:
    jev = FakeJevClient()
    executor = BuildExecutor(make_plan("road", "line", (10, 10), (11, 10)))
    harness = StintHarness(jev, make_brief(max_ticks=4), trace, executor)
    intent = await harness.tick(
        make_observation(1, make_entity("ada", (10, 10), inventory={"road": 2}))
    )
    assert intent.HasField("place")
    assert intent.place.kind == "road"
    assert jev.calls == []


async def test_a_driver_tick_is_traced_without_jevs_numbers(trace: AgentTrace) -> None:
    jev = FakeJevClient()
    executor = BuildExecutor(make_plan("road", "line", (10, 10), (11, 10)))
    harness = StintHarness(jev, make_brief(max_ticks=4), trace, executor)
    await harness.tick(
        make_observation(1, make_entity("ada", (10, 10), inventory={"road": 2}))
    )
    harness.stint.finish("test_over")
    trace.close()
    records = [
        line
        for line in read_lines(trace.directory / "stints.jsonl.gz")
        if "action" in line
    ]
    assert records[0]["driver"] == "build"
    assert records[0]["action"].startswith("build_place:")
    assert "latency_ms" not in records[0]
    # A driver tick made no Jev call, so it must not claim to have cost money.
    assert "cost_usd" not in records[0]
    assert "eject" not in records[0]
    assert records[0]["top"] == []
    # No Jev call means no jev_states line for this tick.
    assert not read_lines(trace.directory / "jev_states.jsonl.gz")


async def test_a_driver_ends_the_stint_with_its_own_reason(trace: AgentTrace) -> None:
    jev = FakeJevClient()
    executor = BuildExecutor(make_plan("road", "line", (10, 10), (11, 10)))
    harness = StintHarness(jev, make_brief(max_ticks=4), trace, executor)
    await harness.tick(make_observation(1, make_entity("ada", (10, 10))))
    assert harness.stint.finished
    assert harness.stint.end_reason == BUILD_OUT_OF_ITEMS


async def test_the_briefs_hails_become_jev_options(trace: AgentTrace) -> None:
    jev = FakeJevClient(default_action="wait")
    brief = make_brief(hails=(BriefHail("mira", "Mira, shall we plan the wall?"),))
    harness = StintHarness(jev, brief, trace)

    await harness.tick(
        make_observation(
            1,
            make_entity("ada", (10, 10)),
            entities=[make_entity("mira", (13, 10))],
        )
    )

    assert "hail:mira" in jev.last_options
    assert "Mira, shall we plan the wall?" in jev.last_options["hail:mira"]


def test_the_brief_payload_carries_the_hails() -> None:
    brief = make_brief(hails=(BriefHail("mira", "Shall we plan the wall?"),))

    assert brief.as_payload()["hails"] == [
        {"settler": "mira", "line": "Shall we plan the wall?"}
    ]


async def test_a_successful_hail_is_dropped_and_reported(trace: AgentTrace) -> None:
    jev = FakeJevClient(default_action="hail:mira")
    brief = make_brief(hails=(BriefHail("mira", "Shall we plan the wall?"),))
    harness = StintHarness(jev, brief, trace)

    await harness.tick(
        make_observation(
            1, make_entity("ada", (10, 10)), entities=[make_entity("mira", (11, 10))]
        )
    )
    await harness.tick(
        make_observation(
            2,
            make_entity("ada", (10, 10)),
            entities=[make_entity("mira", (11, 10))],
            events=[acted_event("ada", "converse", True, "hail conv_1 mira")],
        )
    )

    assert "hail:mira" not in jev.last_options
    report = harness.stint.build_report().to_text()
    assert "hailed mira at tick 2" in report


async def test_two_refusals_drop_the_hail_and_name_the_reason(
    trace: AgentTrace,
) -> None:
    jev = FakeJevClient(default_action="hail:mira")
    brief = make_brief(hails=(BriefHail("mira", "Shall we plan the wall?"),))
    harness = StintHarness(jev, brief, trace)
    refusal = acted_event("ada", "converse", False, "mira is already in conv_2")

    await harness.tick(
        make_observation(
            1, make_entity("ada", (10, 10)), entities=[make_entity("mira", (11, 10))]
        )
    )
    for tick in (2, 3):
        await harness.tick(
            make_observation(
                tick,
                make_entity("ada", (10, 10)),
                entities=[make_entity("mira", (11, 10))],
                events=[refusal],
            )
        )

    assert "hail:mira" not in jev.last_options
    report = harness.stint.build_report().to_text()
    assert "hail to mira refused: mira is already in conv_2" in report
