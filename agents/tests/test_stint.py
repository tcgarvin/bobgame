"""Stint mechanics: Jev's answer becomes an intent, and the code rules end it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.jev_agent.jevclient import JevDecision
from agents.jev_agent.options import TravelState
from agents.jev_agent.stint import (
    END_DEATH,
    END_REPEATED_FAILURE,
    END_SUCCESS_OR_JUDGEMENT,
    END_TICKS,
    Brief,
    Stint,
    default_log_path,
)
from agents import world_pb2 as pb
from agents.jev_agent.worldmodel import WorldModel

from helpers import (
    FakeJevClient,
    acted_event,
    make_entity,
    make_object,
    make_observation,
)


def decision(action: str, *, eject: float = 0.0, danger: float = 0.0) -> JevDecision:
    """A scripted Jev answer."""
    return JevDecision(
        action=action,
        probabilities={action: 0.8, "wait": 0.2},
        confidence=0.7,
        eject=eject,
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

    def __init__(self, jev: FakeJevClient, brief: Brief, log_path: Path) -> None:
        self.model = WorldModel("ada")
        self.jev = jev
        self.stint = Stint(brief, self.model, jev, log_path=log_path)

    async def tick(self, observation) -> object:  # type: ignore[no-untyped-def]
        """Feed one observation through the model and the stint."""
        digest = self.model.update(observation)
        intent = await self.stint.decide(digest)
        self.stint.record_intent_result("accepted")
        return intent


@pytest.fixture
def log_path(tmp_path: Path) -> Path:
    """A throwaway JSONL path."""
    return tmp_path / "logs" / "agent-ada" / "stints.jsonl"


async def test_jev_choice_becomes_the_matching_intent(log_path: Path) -> None:
    jev = FakeJevClient(script=[decision("move_E")])
    harness = StintHarness(jev, make_brief(), log_path)
    intent = await harness.tick(make_observation(1, make_entity("ada", (10, 10))))
    assert intent.HasField("move")
    assert intent.move.direction == 3  # EAST


async def test_extract_choice_targets_the_right_object(log_path: Path) -> None:
    jev = FakeJevClient(script=[decision("extract:tree_1")])
    harness = StintHarness(jev, make_brief(), log_path)
    intent = await harness.tick(
        make_observation(
            1,
            make_entity("ada", (10, 10)),
            objects=[make_object("tree_1", "tree", (11, 10))],
        )
    )
    assert intent.extract.object_id == "tree_1"


async def test_an_option_jev_never_saw_falls_back_to_wait(log_path: Path) -> None:
    jev = FakeJevClient(script=[decision("fly_to_the_moon")])
    harness = StintHarness(jev, make_brief(), log_path)
    intent = await harness.tick(make_observation(1, make_entity("ada", (10, 10))))
    assert intent.HasField("wait")
    assert "unknown option" in harness.stint.records[0].note


async def test_two_high_eject_ticks_in_a_row_end_the_stint(log_path: Path) -> None:
    jev = FakeJevClient(
        script=[
            decision("wait", eject=0.9),
            decision("wait", eject=0.85),
            decision("wait"),
        ]
    )
    harness = StintHarness(jev, make_brief(), log_path)
    await harness.tick(make_observation(1, make_entity("ada", (10, 10))))
    assert not harness.stint.finished
    await harness.tick(make_observation(2, make_entity("ada", (10, 10))))
    assert not harness.stint.finished, "the stint ends on the tick after the streak"
    await harness.tick(make_observation(3, make_entity("ada", (10, 10))))
    assert harness.stint.finished
    assert harness.stint.end_reason == END_SUCCESS_OR_JUDGEMENT


async def test_a_single_high_eject_does_not_end_the_stint(log_path: Path) -> None:
    jev = FakeJevClient(
        script=[decision("wait", eject=0.9), decision("wait", eject=0.1)] * 2
    )
    harness = StintHarness(jev, make_brief(), log_path)
    for tick in range(1, 5):
        await harness.tick(make_observation(tick, make_entity("ada", (10, 10))))
    assert not harness.stint.finished


async def test_the_tick_budget_ends_the_stint(log_path: Path) -> None:
    jev = FakeJevClient()
    harness = StintHarness(jev, make_brief(max_ticks=2), log_path)
    await harness.tick(make_observation(1, make_entity("ada", (10, 10))))
    await harness.tick(make_observation(2, make_entity("ada", (10, 10))))
    assert not harness.stint.finished
    await harness.tick(make_observation(3, make_entity("ada", (10, 10))))
    assert harness.stint.finished
    assert harness.stint.end_reason == END_TICKS
    assert harness.stint.ticks_used == 2


async def test_death_ends_the_stint(log_path: Path) -> None:
    jev = FakeJevClient()
    harness = StintHarness(jev, make_brief(), log_path)
    await harness.tick(make_observation(1, make_entity("ada", (10, 10))))
    await harness.tick(
        make_observation(2, make_entity("ada", (10, 10), health=0, alive=False))
    )
    assert harness.stint.finished
    assert harness.stint.end_reason == END_DEATH


async def test_three_identical_failures_end_the_stint(log_path: Path) -> None:
    jev = FakeJevClient(default_action="move_N")
    harness = StintHarness(jev, make_brief(max_ticks=20), log_path)
    failure = acted_event("ada", "move", False, "blocked")
    await harness.tick(make_observation(1, make_entity("ada", (10, 10))))
    for tick in range(2, 5):
        await harness.tick(
            make_observation(tick, make_entity("ada", (10, 10)), events=[failure])
        )
    assert harness.stint.finished
    assert harness.stint.end_reason == END_REPEATED_FAILURE


async def test_a_success_resets_the_failure_streak(log_path: Path) -> None:
    jev = FakeJevClient(default_action="move_N")
    harness = StintHarness(jev, make_brief(max_ticks=20), log_path)
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


async def test_danger_rule_overrides_jev_when_death_is_close(log_path: Path) -> None:
    jev = FakeJevClient(script=[decision("wait", danger=0.95)])
    harness = StintHarness(jev, make_brief(), log_path)
    intent = await harness.tick(
        make_observation(
            1,
            make_entity("ada", (10, 10), health=4),
            entities=[make_entity("wolf_1", (11, 10), entity_type="wolf")],
        )
    )
    assert intent.HasField("move")
    assert harness.stint.records[0].note.startswith("danger override")


async def test_danger_rule_leaves_healthy_actors_alone(log_path: Path) -> None:
    jev = FakeJevClient(script=[decision("wait", danger=0.95)])
    harness = StintHarness(jev, make_brief(), log_path)
    intent = await harness.tick(
        make_observation(
            1,
            make_entity("ada", (10, 10), health=18),
            entities=[make_entity("wolf_1", (11, 10), entity_type="wolf")],
        )
    )
    assert intent.HasField("wait")


async def test_a_jev_failure_falls_back_to_wait_without_crashing(
    log_path: Path,
) -> None:
    class BrokenJev:
        async def decide(self, state, options):  # type: ignore[no-untyped-def]
            raise RuntimeError("typesafe is down")

    model = WorldModel("ada")
    stint = Stint(make_brief(), model, BrokenJev(), log_path=log_path)
    digest = model.update(make_observation(1, make_entity("ada", (10, 10))))
    intent = await stint.decide(digest)
    stint.record_intent_result("accepted")
    assert intent.HasField("wait")
    assert "jev_error" in stint.records[0].note
    assert not stint.finished


async def test_check_every_repeats_the_last_action_between_calls(
    log_path: Path,
) -> None:
    jev = FakeJevClient(default_action="move_E")
    harness = StintHarness(jev, make_brief(max_ticks=6, check_every=2), log_path)
    for tick in range(1, 5):
        # The actor really moves east each tick, so no move counts as blocked.
        await harness.tick(make_observation(tick, make_entity("ada", (9 + tick, 10))))
    assert len(jev.calls) == 2, "Jev is asked every other tick"
    assert [record.action for record in harness.stint.records] == ["move_E"] * 4
    assert harness.stint.records[1].note == "repeat"


async def test_accepted_move_that_goes_nowhere_counts_as_blocked(
    log_path: Path,
) -> None:
    jev = FakeJevClient(default_action="move_E")
    harness = StintHarness(jev, make_brief(max_ticks=20), log_path)
    for tick in range(1, 5):
        await harness.tick(make_observation(tick, make_entity("ada", (10, 10))))
    results = [record.intent_result for record in harness.stint.records]
    assert results[:3] == ["blocked", "blocked", "blocked"]
    assert harness.stint.finished, "three blocked moves in a row end the stint"
    assert any("blocked" in line for line in harness.model.recent_history())


async def test_travel_choice_sets_and_clears_the_travel(log_path: Path) -> None:
    jev = FakeJevClient(script=[decision("travel_to:tree_1"), decision("stop_travel")])
    harness = StintHarness(jev, make_brief(), log_path)
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


async def test_a_preset_travel_offers_follow_travel_on_the_first_tick(
    log_path: Path,
) -> None:
    jev = FakeJevClient(default_action="follow_travel")
    brief = make_brief(travel=TravelState(target=(14, 10), label="(14, 10)"))
    harness = StintHarness(jev, brief, log_path)
    intent = await harness.tick(make_observation(1, make_entity("ada", (10, 10))))
    assert "follow_travel" in jev.last_options
    assert intent.move.direction == 3  # EAST


async def test_every_tick_is_written_to_the_jsonl_log(log_path: Path) -> None:
    jev = FakeJevClient(script=[decision("move_E"), decision("wait")])
    harness = StintHarness(jev, make_brief(), log_path)
    await harness.tick(make_observation(1, make_entity("ada", (10, 10))))
    await harness.tick(make_observation(2, make_entity("ada", (11, 10))))

    lines = log_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["entity_id"] == "ada"
    assert first["tick"] == 1
    assert first["action"] == "move_E"
    assert first["input_tokens"] == 420
    assert first["latency_ms"] == 250
    assert first["intent_result"] == "accepted"
    assert first["options"] > 5
    assert first["top"][0][0] == "move_E"


async def test_the_report_summarises_the_whole_stint(log_path: Path) -> None:
    jev = FakeJevClient(default_action="move_E")
    harness = StintHarness(jev, make_brief(max_ticks=2), log_path)
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


async def test_the_report_mentions_damage_and_speech(log_path: Path) -> None:
    jev = FakeJevClient()
    harness = StintHarness(jev, make_brief(max_ticks=4), log_path)
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


def test_default_log_path_follows_the_documented_layout(tmp_path: Path) -> None:
    assert default_log_path("ada", tmp_path) == tmp_path / "agent-ada" / "stints.jsonl"
    assert default_log_path("ada") == Path("logs/agent-ada/stints.jsonl")


async def test_status_json_reports_the_last_decision(log_path: Path) -> None:
    jev = FakeJevClient(script=[decision("move_E", eject=0.4, danger=0.2)])
    harness = StintHarness(jev, make_brief(), log_path)
    await harness.tick(make_observation(1, make_entity("ada", (10, 10))))
    payload = json.loads(harness.stint.status_json())
    assert payload["action"] == "move_E"
    assert payload["eject"] == 0.4
    assert payload["danger"] == 0.2
    assert payload["ticks_used"] == 1


async def test_a_slow_jev_answer_falls_back_to_repeating_the_last_action(
    log_path: Path, monkeypatch: pytest.MonkeyPatch
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
    harness = StintHarness(jev, make_brief(max_ticks=10), log_path)
    await harness.tick(make_observation(1, make_entity("ada", (10, 10))))
    intent = await harness.tick(make_observation(2, make_entity("ada", (11, 10))))
    assert intent.HasField("move"), "the last action is repeated on timeout"
    assert harness.stint.records[-1].note == "jev_timeout"
