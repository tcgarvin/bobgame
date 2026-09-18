"""The compact state Jev sees: shape, glyphs, and size."""

from __future__ import annotations

import json

from agents.jev_agent.jevstate import MAP_LEGEND, MAP_SIZE, build_state, render_map
from agents.jev_agent.options import TravelState
from agents.jev_agent.worldmodel import WorldModel

from helpers import (
    acted_event,
    make_entity,
    make_object,
    make_observation,
    make_tiles,
    utterance_event,
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


# --- building (docs/08_building.md) ----------------------------------------


def test_the_map_shows_the_new_objects_and_the_legend_explains_them() -> None:
    model = WorldModel("ada")
    model.update(
        make_observation(
            1,
            make_entity("ada", (10, 10)),
            objects=[
                make_object("r1", "reeds", (11, 10)),
                make_object("c1", "clay_deposit", (12, 10)),
                make_object("w1", "wood_wall", (10, 9)),
                make_object("d1", "door", (9, 10)),
                make_object("b1", "bed", (10, 11)),
                make_object("ws1", "workshop_table", (9, 9)),
            ],
        )
    )
    rows = render_map(model).splitlines()
    centre = len(rows) // 2
    assert rows[centre][centre + 1] == "r"
    assert rows[centre][centre + 2] == "y"
    assert rows[centre - 1][centre] == "#"
    assert rows[centre][centre - 1] == "+"
    assert rows[centre + 1][centre] == "z"
    assert rows[centre - 1][centre - 1] == "X"
    for glyph in ("r reeds", "y clay", "+ door", "z bed", "X workshop table"):
        assert glyph in MAP_LEGEND


def test_a_structure_is_drawn_over_the_floor_it_stands_on() -> None:
    model = WorldModel("ada")
    model.update(
        make_observation(
            1,
            make_entity("ada", (10, 10)),
            objects=[
                make_object("f1", "wood_floor", (11, 10)),
                make_object("b1", "bed", (11, 10)),
                make_object("r1", "road", (12, 10)),
            ],
        )
    )
    rows = render_map(model).splitlines()
    centre = len(rows) // 2
    assert rows[centre][centre + 1] == "z"
    assert rows[centre][centre + 2] == ","


def test_the_state_says_whether_a_workshop_table_is_within_reach() -> None:
    model = WorldModel("ada")
    model.update(
        make_observation(
            1,
            make_entity("ada", (10, 10)),
            objects=[make_object("ws1", "workshop_table", (11, 10))],
        )
    )
    state = build_state(
        model, instruction="craft", success_condition="a bed exists", ticks_left=4
    )
    assert state["self"]["at_workshop_table"] is True


def test_reeds_report_what_they_yield() -> None:
    model = WorldModel("ada")
    model.update(
        make_observation(
            1,
            make_entity("ada", (10, 10)),
            objects=[make_object("r1", "reeds", (11, 10))],
        )
    )
    state = build_state(
        model, instruction="gather", success_condition="6 fiber", ticks_left=4
    )
    entry = next(item for item in state["nearby"] if item["id"] == "r1")
    assert entry["yields"] == "fiber"
    assert entry["remaining"] == 3


# --- names, threats and shouts -------------------------------------------------


def _state(model: WorldModel) -> dict[str, object]:
    return build_state(
        model, instruction="Chop", success_condition="4 wood", ticks_left=10
    )


def test_jev_is_told_its_own_name() -> None:
    assert _state(build_model())["self"]["name"] == "ada"  # type: ignore[index]


def test_no_threat_block_without_a_wolf_in_view() -> None:
    model = WorldModel("ada")
    model.update(make_observation(1, make_entity("ada", (10, 10))))
    assert "threat" not in _state(model)


def test_the_threat_block_counts_allies_and_states_wolf_physics_only() -> None:
    model = WorldModel("ada")
    model.update(
        make_observation(
            1,
            make_entity("ada", (10, 10)),
            entities=[
                make_entity("wolf_1", (13, 10), entity_type="wolf", health=9),
                make_entity("bram", (12, 10), wielded="sword"),
                make_entity("cleo", (11, 12)),
            ],
        )
    )
    state = _state(model)
    threat = state["threat"]
    assert isinstance(threat, dict)
    assert threat["nearest_wolf"]["id"] == "wolf_1"
    assert threat["nearest_wolf"]["dx"] == 3
    assert threat["nearest_wolf"]["settlers_next_to_it"] == 1
    assert threat["settlers_within_3_of_you"] == 2
    assert "16 health" in threat["facts"]
    assert "Shout" not in threat["facts"] and "allies" not in threat["facts"]
    wielded = {e["id"]: e["wielded"] for e in state["entities"]}  # type: ignore[union-attr]
    assert wielded["bram"] == "sword"


def test_a_heard_shout_says_where_it_came_from() -> None:
    model = WorldModel("ada")
    model.update(
        make_observation(
            7,
            make_entity("ada", (10, 10)),
            events=[
                utterance_event("bram", "Wolf!", (40, 4), channel="shout"),
                utterance_event("cleo", "hello", (11, 10)),
            ],
        )
    )
    heard = _state(model)["heard"]
    assert heard == [
        "bram shouted from dx 30 dy -6, 0 ticks ago: Wolf!",
        "cleo: hello",
    ]


# --- fatigue, sleep and the clock (docs/10_metal_and_sleep.md) --------------


def _state_for(entity: object, tick: int = 41, **kwargs: object) -> dict:
    """The Jev state for one entity after a single observation."""
    model = WorldModel("ada")
    model.update(make_observation(tick, entity, **kwargs))  # type: ignore[arg-type]
    return build_state(model, instruction="do", success_condition="done", ticks_left=5)


def test_the_state_reports_fatigue_as_a_number_and_a_word() -> None:
    fresh = _state_for(make_entity("ada", (10, 10), fatigue=12))
    assert fresh["self"]["fatigue"] == "12/100 (fresh)"
    assert fresh["self"]["asleep"] is False

    tired = _state_for(make_entity("ada", (10, 10), fatigue=75))
    assert tired["self"]["fatigue"] == "75/100 (tired)"

    spent = _state_for(make_entity("ada", (10, 10), fatigue=100, asleep=True))
    assert spent["self"]["fatigue"] == "100/100 (exhausted)"
    assert spent["self"]["asleep"] is True


def test_the_state_carries_the_clock_and_the_day_physics() -> None:
    state = _state_for(make_entity("ada", (10, 10)), tick=250)
    assert state["clock"]["day"] == 0
    assert state["clock"]["tick_of_day"] == "250/300"
    assert state["clock"]["night"] is True
    assert "300 ticks" in state["clock"]["facts"]


def test_the_state_states_the_fatigue_physics_without_advice() -> None:
    facts = _state_for(make_entity("ada", (10, 10)))["fatigue_facts"]
    assert "1 every 4 ticks by day" in facts
    assert "From 60 you are tired" in facts
    assert "At 100 you collapse" in facts
    assert "1 fatigue per tick" in facts


def test_the_state_says_which_stations_are_within_reach() -> None:
    state = _state_for(
        make_entity("ada", (10, 10)),
        objects=[
            make_object("fur1", "furnace", (11, 10)),
            make_object("anv1", "anvil", (9, 10)),
        ],
    )
    assert state["self"]["at_furnace"] is True
    assert state["self"]["at_anvil"] is True
    assert state["self"]["at_workshop_table"] is False


def test_veins_show_their_units_and_what_they_yield() -> None:
    state = _state_for(
        make_entity("ada", (10, 10)),
        objects=[make_object("v1", "iron_vein", (11, 10))],
    )
    entry = next(item for item in state["nearby"] if item["id"] == "v1")
    assert entry["yields"] == "iron_ore"
    assert entry["remaining"] == 4
