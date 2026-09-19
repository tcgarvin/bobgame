"""The reflex brief: when it fires, when it stops, and how it is persisted."""

from __future__ import annotations

from pathlib import Path

from agents.jev_agent.reflex import (
    EMPTY_REFLEX,
    REFLEX_CLEAR_TICKS,
    REFLEX_COOLDOWN_TICKS,
    TRIGGER_DAMAGE,
    TRIGGER_WOLF_NEAR,
    END_THREAT_GONE,
    NO_REFLEX_LINE,
    ReflexBrief,
    ReflexStore,
    ReflexWatch,
    clamp_trigger_distance,
    reflex_report_line,
)
from agents.jev_agent.worldmodel import WorldModel

from helpers import damaged_event, make_entity, make_observation

GUARD = ReflexBrief(
    instruction="Stand next to the workshop table.",
    success_condition="you are next to the workshop table",
    max_ticks=20,
    trigger_distance=4,
    notes="eat a berry below food 40",
    shouts=("Someone come to the table.",),
)


def advance(
    model: WorldModel,
    tick: int,
    *,
    wolf_at: tuple[int, int] | None = None,
    bitten_by: str = "",
) -> object:
    """Feed the model one observation and return the tick digest."""
    entities = (
        [] if wolf_at is None else [make_entity("wolf_1", wolf_at, entity_type="wolf")]
    )
    events = [damaged_event("ada", bitten_by, 3, 17)] if bitten_by else []
    return model.update(
        make_observation(
            tick,
            make_entity("ada", (10, 10)),
            entities=entities,
            events=events,
        )
    )


def test_a_wolf_inside_the_trigger_distance_fires_the_reflex() -> None:
    model = WorldModel("ada")
    watch = ReflexWatch(GUARD)
    digest = advance(model, 1, wolf_at=(13, 10))
    assert watch.trigger(model, digest) == TRIGGER_WOLF_NEAR


def test_a_wolf_beyond_the_trigger_distance_does_not_fire_it() -> None:
    model = WorldModel("ada")
    watch = ReflexWatch(GUARD)
    digest = advance(model, 1, wolf_at=(16, 10))
    assert watch.trigger(model, digest) == ""


def test_without_a_brief_nothing_ever_fires() -> None:
    model = WorldModel("ada")
    watch = ReflexWatch(EMPTY_REFLEX)
    digest = advance(model, 1, wolf_at=(10, 11), bitten_by="wolf_1")
    assert watch.trigger(model, digest) == ""


def test_damage_fires_the_reflex_and_ignores_the_cooldown() -> None:
    model = WorldModel("ada")
    watch = ReflexWatch(GUARD)
    watch.note_end(1)
    digest = advance(model, 2, bitten_by="wolf_1")
    assert watch.trigger(model, digest) == TRIGGER_DAMAGE


def test_the_distance_trigger_waits_out_the_cooldown() -> None:
    model = WorldModel("ada")
    watch = ReflexWatch(GUARD)
    watch.note_end(10)

    digest = advance(model, 10 + REFLEX_COOLDOWN_TICKS - 1, wolf_at=(12, 10))
    assert watch.trigger(model, digest) == ""

    digest = advance(model, 10 + REFLEX_COOLDOWN_TICKS, wolf_at=(12, 10))
    assert watch.trigger(model, digest) == TRIGGER_WOLF_NEAR


def test_the_stint_ends_once_no_wolf_has_been_in_view_for_three_ticks() -> None:
    model = WorldModel("ada")
    watch = ReflexWatch(GUARD)
    watch.begin()

    advance(model, 1, wolf_at=(12, 10))
    assert watch.end_reason(model) == ""
    for tick in range(2, 1 + REFLEX_CLEAR_TICKS):
        advance(model, tick)
        assert watch.end_reason(model) == ""
    advance(model, 1 + REFLEX_CLEAR_TICKS)
    assert watch.end_reason(model) == END_THREAT_GONE


def test_a_wolf_coming_back_restarts_the_clear_count() -> None:
    model = WorldModel("ada")
    watch = ReflexWatch(GUARD)
    watch.begin()
    advance(model, 1)
    watch.end_reason(model)
    advance(model, 2, wolf_at=(12, 10))
    assert watch.end_reason(model) == ""
    advance(model, 3)
    assert watch.end_reason(model) == ""


def test_the_trigger_distance_is_clamped_to_the_legal_range() -> None:
    assert clamp_trigger_distance(0) == 1
    assert clamp_trigger_distance(99) == 8
    assert clamp_trigger_distance(5) == 5


def test_the_brief_round_trips_through_the_reflex_file(tmp_path: Path) -> None:
    store = ReflexStore(tmp_path / "reflex.json")
    assert store.load() == EMPTY_REFLEX

    store.save(GUARD)
    assert store.load() == GUARD

    store.save(EMPTY_REFLEX)
    assert not (tmp_path / "reflex.json").exists()
    assert store.load() == EMPTY_REFLEX


def test_a_corrupt_reflex_file_reads_as_no_reflex(tmp_path: Path) -> None:
    path = tmp_path / "reflex.json"
    path.write_text("{not json", encoding="utf-8")
    assert ReflexStore(path).load() == EMPTY_REFLEX


def test_the_prompt_line_says_whether_there_is_a_reflex() -> None:
    assert EMPTY_REFLEX.prompt_line() == NO_REFLEX_LINE
    line = GUARD.prompt_line()
    assert GUARD.instruction in line
    assert "trigger_distance 4" in line


def test_the_brief_becomes_an_ordinary_stint_brief() -> None:
    brief = GUARD.to_brief()
    assert brief.instruction == GUARD.instruction
    assert brief.max_ticks == GUARD.max_ticks
    assert brief.shouts == GUARD.shouts


def test_the_report_line_has_the_shape_the_planner_is_promised() -> None:
    line = reflex_report_line(10, 18, END_THREAT_GONE, 20, 14)
    assert line == (
        "[reflex ran ticks 10-18: ended because threat_gone; health 20 -> 14]"
    )


def test_named_places_survive_a_save_and_an_old_file_still_loads(
    tmp_path: Path,
) -> None:
    """`reflex.json` written before places existed must keep working."""
    store = ReflexStore(tmp_path / "reflex.json")
    with_places = ReflexBrief(
        instruction="Run to the gate.",
        success_condition="you are at the gate",
        max_ticks=10,
        trigger_distance=4,
        places={"the_gate": (1539, 974)},
    )
    store.save(with_places)
    assert store.load() == with_places
    assert store.load().to_brief().places == {"the_gate": (1539, 974)}

    (tmp_path / "reflex.json").write_text(
        '{"instruction": "Run.", "success_condition": "safe", "max_ticks": 5, '
        '"trigger_distance": 3}',
        encoding="utf-8",
    )
    assert store.load().places == {}
