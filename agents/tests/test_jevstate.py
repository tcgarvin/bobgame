"""The compact state Jev sees: shape, glyphs, and size."""

from __future__ import annotations

import json

from agents.jev_agent.jevstate import (
    MAP_LEGEND,
    MAP_SIZE,
    StintProgress,
    build_state,
    collapse_action,
    render_map,
)
from agents.jev_agent.options import BriefHail, TravelState
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
                food=35,
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
    assert glyph(0, 2) == "B"  # bush_3 at (10, 12) has a berry
    assert glyph(-1, 0) == "C"  # chest_2 at (9, 10)
    assert glyph(1, 1) == "M"  # board_1 at (11, 11)
    assert glyph(2, 0) == "i"  # pile_1 at (12, 10)
    assert glyph(-3, 0) == "o"  # rock_4 at (7, 10)
    assert glyph(-2, 0) == "W"  # wolf_1 at (8, 10)
    assert glyph(2, 2) == "P"  # bob at (12, 12)
    assert glyph(-2, -2) == "#"  # blocked tile at (8, 8)
    assert glyph(1, 0) == "."  # plain ground


def test_a_bush_with_a_berry_and_one_without_have_their_own_glyphs() -> None:
    """The only glyph Jev acts on directly, so it must say whether to bother."""
    model = WorldModel("ada")
    model.update(
        make_observation(
            1,
            make_entity("ada", (10, 10)),
            objects=[
                make_object("full", "bush", (11, 10), {"berry_count": "1"}),
                make_object("empty", "bush", (12, 10), {"berry_count": "0"}),
            ],
        )
    )
    rows = render_map(model).splitlines()
    centre = len(rows) // 2
    assert rows[centre][centre + 1] == "B"
    assert rows[centre][centre + 2] == "b"
    assert "B bush with a berry" in MAP_LEGEND
    assert "b bush with no berry" in MAP_LEGEND


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
        notes="wolves are dangerous below 8 health",
    )
    assert state["brief"] == {
        "instruction": "Chop wood",
        "success_condition": "you hold 5 wood",
    }
    assert state["self"]["health"] == "14/20"
    assert state["self"]["food"] == "35/100"
    assert state["self"]["wielded"] == "axe"
    assert state["self"]["inventory"] == {"wood": 3}
    assert state["settlement"] == {"dx": 0, "dy": 0}
    assert state["notes"] == "wolves are dangerous below 8 health"
    assert state["travel"] is None
    assert state["recent"] == ["t41 extract ok (chopped tree_9 (+1 wood))"]


def test_nearby_entries_use_relative_offsets_and_type_details() -> None:
    state = build_state(build_model(), instruction="x", success_condition="y")
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
        travel=TravelState(target=(14, 10), label="the clearing"),
    )
    assert state["travel"]["next_step"] == "E"
    assert state["travel"]["steps_left"] == 4
    assert "dx 4 dy 0" in state["travel"]["target"]


def test_state_stays_well_under_the_token_budget() -> None:
    state = build_state(
        build_model(), instruction="Chop wood", success_condition="5 wood"
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
    state = build_state(model, instruction="craft", success_condition="a bed exists")
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
    state = build_state(model, instruction="gather", success_condition="6 fiber")
    entry = next(item for item in state["nearby"] if item["id"] == "r1")
    assert entry["yields"] == "fiber"
    assert entry["remaining"] == 3


# --- names, threats and shouts -------------------------------------------------


def _state(model: WorldModel) -> dict[str, object]:
    return build_state(model, instruction="Chop", success_condition="4 wood")


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
    assert "facts" not in threat
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
    return build_state(model, instruction="do", success_condition="done")


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
    assert "facts" not in state["clock"]


def test_the_facts_are_one_always_present_list_in_a_fixed_order() -> None:
    facts = _state_for(make_entity("ada", (10, 10)))["facts"]
    assert len(facts) == 4
    # Food first: it is the one that kills a settler that nothing attacks.
    assert facts[0].startswith("Food falls 1 every 4 ticks.")
    assert "Eating a berry restores 20 food" in facts[0]
    assert "bushes marked B on the map" in facts[0]
    assert "16 health" in facts[1]
    assert "From 60 you are tired" in facts[2]
    assert "300 ticks" in facts[3]
    assert "fatigue_facts" not in _state_for(make_entity("ada", (10, 10)))


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


# --- brief hails (docs/09 section 9.3) --------------------------------------


def test_the_brief_block_names_each_hail_and_its_line() -> None:
    model = WorldModel("ada")
    model.update(make_observation(41, make_entity("ada", (10, 10))))

    state = build_state(
        model,
        instruction="do",
        success_condition="done",
        hails=[BriefHail("mira", "Plan the wall?")],
    )

    assert state["brief"]["hails"] == ['say to mira: "Plan the wall?"']


def test_the_brief_block_has_no_hails_key_when_there_are_none() -> None:
    assert "hails" not in _state_for(make_entity("ada", (10, 10)))["brief"]


def test_a_sleeping_neighbour_is_marked_asleep_and_the_others_are_not() -> None:
    model = WorldModel("ada")
    model.update(
        make_observation(
            7,
            make_entity("ada", (10, 10)),
            entities=[
                make_entity("mira", (11, 10), asleep=True),
                make_entity("bran", (9, 10)),
            ],
        )
    )
    state = build_state(model, instruction="", success_condition="")
    by_id = {entry["id"]: entry for entry in state["entities"]}
    assert by_id["mira"]["asleep"] is True
    assert "asleep" not in by_id["bran"]


# --- what the map cannot say (2026-09-18 rework) ----------------------------


def test_nearby_drops_the_trees_the_map_already_draws() -> None:
    """A grove used to fill all 25 slots and push the bush and chest out."""
    model = WorldModel("ada")
    model.update(
        make_observation(
            1,
            make_entity("ada", (10, 10)),
            objects=[make_object(f"tree_{i}", "tree", (10, 4 + i)) for i in range(5)]
            + [
                make_object("bush_1", "bush", (14, 14), {"berry_count": "1"}),
                make_object("chest_1", "chest", (16, 16), {"contents": '{"wood": 1}'}),
                make_object("wall_1", "wood_wall", (12, 10)),
            ],
        )
    )
    ids = [
        entry["id"]
        for entry in build_state(model, instruction="", success_condition="")["nearby"]
    ]
    assert len([i for i in ids if i.startswith("tree_")]) == 1, "one tree is enough"
    assert "bush_1" in ids
    assert "chest_1" in ids, "a chest is never only a glyph"
    assert "wall_1" not in ids, "the map says everything a wall has to say"


def test_an_object_the_brief_names_stays_in_nearby() -> None:
    model = WorldModel("ada")
    model.update(
        make_observation(
            1,
            make_entity("ada", (10, 10)),
            objects=[make_object(f"tree_{i}", "tree", (10, 4 + i)) for i in range(5)],
        )
    )
    state = build_state(
        model, instruction="", success_condition="", highlight_ids=["tree_3"]
    )
    assert "tree_3" in [entry["id"] for entry in state["nearby"]]


def test_the_named_places_are_shown_as_offsets_and_never_as_coordinates() -> None:
    state = build_state(
        build_model(),
        instruction="Walk to the destination.",
        success_condition="you are there",
        places={"destination": (14, 7)},
    )
    assert state["brief"]["places"] == {"destination": "dx 4 dy -3"}
    assert "14" not in json.dumps(state["brief"])


def test_the_so_far_block_reports_what_code_measured() -> None:
    progress = StintProgress(
        ticks_used=12,
        ticks_left=8,
        inventory_change={"stone": 2},
        actions={"extract": 10, "move": 2},
        moved_from_start=(0, -1),
        net_tiles_moved=1,
    )
    state = build_state(
        build_model(), instruction="", success_condition="", progress=progress
    )
    assert state["so_far"] == {
        "ticks_used": 12,
        "ticks_left": 8,
        "inventory_change": {"stone": 2},
        "actions": {"extract": 10, "move": 2},
        "moved_from_start": "dx 0 dy -1",
        "net_tiles_moved": 1,
    }
    assert "ticks_left" not in state["brief"]


def test_action_names_collapse_to_one_entry_per_kind() -> None:
    assert collapse_action("move_NW") == "move"
    assert collapse_action("step_towards:tree_9") == "step_towards"
    assert collapse_action("extract:tree_9") == "extract"
    assert collapse_action("keep_going") == "keep_going"


def test_a_reached_target_says_arrived_and_an_unreachable_one_says_blocked() -> None:
    model = build_model()
    arrived = build_state(
        model,
        instruction="",
        success_condition="",
        travel=TravelState(target=(11, 10), label="tree_9", stop_adjacent=True),
    )
    assert arrived["travel"]["next_step"] == "arrived"
    assert arrived["travel"]["steps_left"] == 0

    walled_in = WorldModel("ada")
    walled_in.update(
        make_observation(
            1,
            make_entity("ada", (10, 10)),
            tiles=make_tiles(
                (10, 10),
                radius=2,
                blocked=[
                    (x, y)
                    for x in range(9, 12)
                    for y in range(9, 12)
                    if (x, y) != (10, 10)
                ],
            ),
        )
    )
    blocked = build_state(
        walled_in,
        instruction="",
        success_condition="",
        travel=TravelState(target=(12, 12), label="the shore"),
    )
    assert blocked["travel"]["next_step"] == "blocked"
    assert blocked["travel"]["steps_left"] is None
