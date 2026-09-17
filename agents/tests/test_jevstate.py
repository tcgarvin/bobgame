"""The compact state Jev sees: shape, glyphs, and size."""

from __future__ import annotations

import json

from agents.jev_agent.jevstate import MAP_SIZE, build_state, render_map
from agents.jev_agent.options import TravelState
from agents.jev_agent.worldmodel import WorldModel

from helpers import (
    acted_event,
    make_entity,
    make_object,
    make_observation,
    make_tiles,
)


def build_model() -> WorldModel:
    """A populated model for `ada` at (10, 10)."""
    model = WorldModel("ada")
    model.update(
        make_observation(
            41,
            make_entity(
                "ada",
                (10, 10),
                health=14,
                hunger=35,
                wielded="axe",
                inventory={"wood": 3},
            ),
            tiles=make_tiles((10, 10), blocked=[(8, 8)]),
            objects=[
                make_object("tree_9", "tree", (13, 8)),
                make_object("bush_3", "bush", (10, 12), {"berry_count": "1"}),
                make_object("chest_2", "chest", (9, 10), {"contents": '{"wood": 5}'}),
                make_object("board_1", "message_board", (11, 11)),
                make_object(
                    "pile_1", "item_pile", (12, 10), {"contents": '{"stone": 1}'}
                ),
                make_object("rock_4", "rock_medium", (7, 10)),
            ],
            entities=[
                make_entity(
                    "wolf_1", (8, 10), entity_type="wolf", max_health=10, health=10
                ),
                make_entity("bob", (12, 12)),
            ],
            events=[acted_event("ada", "extract", True, "chopped tree_9 (+1 wood)")],
        )
    )
    return model


def test_map_is_square_and_centred_on_the_actor() -> None:
    rows = render_map(build_model()).split("\n")
    assert len(rows) == MAP_SIZE
    assert all(len(row) == MAP_SIZE for row in rows)
    centre = MAP_SIZE // 2
    assert rows[centre][centre] == "@"


def test_map_glyphs_match_the_legend() -> None:
    rows = render_map(build_model()).split("\n")
    centre = MAP_SIZE // 2

    def glyph(dx: int, dy: int) -> str:
        return rows[centre + dy][centre + dx]

    assert glyph(3, -2) == "T"  # tree_9 at (13, 8)
    assert glyph(0, 2) == "b"  # bush_3 at (10, 12)
    assert glyph(-1, 0) == "C"  # chest_2 at (9, 10)
    assert glyph(1, 1) == "B"  # board_1 at (11, 11)
    assert glyph(2, 0) == "i"  # pile_1 at (12, 10)
    assert glyph(-3, 0) == "o"  # rock_4 at (7, 10)
    assert glyph(-2, 0) == "W"  # wolf_1 at (8, 10)
    assert glyph(2, 2) == "P"  # bob at (12, 12)
    assert glyph(-2, -2) == "#"  # blocked tile at (8, 8)
    assert glyph(1, 0) == "."  # plain ground


def test_unobserved_tiles_render_as_question_marks() -> None:
    model = WorldModel("ada")
    model.update(
        make_observation(
            1, make_entity("ada", (0, 0)), tiles=make_tiles((0, 0), radius=1)
        )
    )
    rows = render_map(model).split("\n")
    assert rows[0][0] == "?"


def test_state_carries_the_brief_self_and_settlement_offset() -> None:
    model = build_model()
    state = build_state(
        model,
        instruction="Chop wood",
        success_condition="you hold 5 wood",
        ticks_left=12,
        notes="wolves are dangerous below 8 health",
    )
    assert state["brief"] == {
        "instruction": "Chop wood",
        "success_condition": "you hold 5 wood",
        "ticks_left": 12,
    }
    assert state["self"]["health"] == "14/20"
    assert state["self"]["hunger"] == "35/100"
    assert state["self"]["wielded"] == "axe"
    assert state["self"]["inventory"] == {"wood": 3}
    assert state["settlement"] == {"dx": 0, "dy": 0}
    assert state["notes"] == "wolves are dangerous below 8 health"
    assert state["travel"] is None
    assert state["recent"] == ["t41 extract ok (chopped tree_9 (+1 wood))"]


def test_nearby_entries_use_relative_offsets_and_type_details() -> None:
    state = build_state(
        build_model(), instruction="x", success_condition="y", ticks_left=1
    )
    by_id = {entry["id"]: entry for entry in state["nearby"]}
    assert by_id["tree_9"]["dx"] == 3 and by_id["tree_9"]["dy"] == -2
    assert by_id["tree_9"]["remaining"] == 4
    assert by_id["bush_3"]["berry"] is True
    assert by_id["chest_2"]["contents"] == {"wood": 5}

    entities = {entry["id"]: entry for entry in state["entities"]}
    assert entities["wolf_1"]["dx"] == -2
    assert entities["wolf_1"]["health"] == "10/10"


def test_travel_entry_reports_the_next_step_and_distance() -> None:
    model = build_model()
    state = build_state(
        model,
        instruction="x",
        success_condition="y",
        ticks_left=3,
        travel=TravelState(target=(14, 10), label="the clearing"),
    )
    assert state["travel"]["next_step"] == "E"
    assert state["travel"]["steps_left"] == 4
    assert "dx 4 dy 0" in state["travel"]["target"]


def test_state_stays_well_under_the_token_budget() -> None:
    state = build_state(
        build_model(), instruction="Chop wood", success_condition="5 wood", ticks_left=9
    )
    approximate_tokens = len(json.dumps(state)) // 4
    assert approximate_tokens < 3000
