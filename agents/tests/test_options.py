"""Only legal options reach Jev, and the list stays inside its budget."""

from __future__ import annotations

from agents.jev_agent.options import (
    MAX_OPTIONS,
    SAY_PHRASES,
    TravelState,
    enumerate_options,
    options_to_criteria,
    retreat_option,
)
from agents.jev_agent.worldmodel import WorldModel

from helpers import make_entity, make_object, make_observation, make_tiles


def build_model(**observation_kwargs: object) -> WorldModel:
    """A world model for `ada` at (10, 10) after one observation."""
    model = WorldModel("ada")
    self_entity = observation_kwargs.pop("self_entity", make_entity("ada", (10, 10)))
    model.update(make_observation(1, self_entity, **observation_kwargs))  # type: ignore[arg-type]
    return model


def keys(model: WorldModel, travel: TravelState | None = None) -> list[str]:
    """Option keys for a model, in enumeration order."""
    return [option.key for option in enumerate_options(model, travel)]


def test_wait_is_always_offered() -> None:
    assert "wait" in keys(build_model())


def test_all_eight_moves_are_offered_on_open_ground() -> None:
    offered = keys(build_model())
    for name in ("N", "NE", "E", "SE", "S", "SW", "W", "NW"):
        assert f"move_{name}" in offered


def test_blocked_directions_are_not_offered() -> None:
    model = build_model(tiles=make_tiles((10, 10), blocked=[(10, 9), (11, 10)]))
    offered = keys(model)
    assert "move_N" not in offered
    assert "move_E" not in offered
    # NE needs both N and E, so it goes too.
    assert "move_NE" not in offered
    assert "move_S" in offered


def test_extract_is_offered_only_for_adjacent_trees_and_rocks() -> None:
    model = build_model(
        objects=[
            make_object("tree_near", "tree", (11, 10)),
            make_object("tree_far", "tree", (15, 10)),
            make_object("rock_1", "rock_small", (10, 11)),
        ]
    )
    offered = keys(model)
    assert "extract:tree_near" in offered
    assert "extract:rock_1" in offered
    assert "extract:tree_far" not in offered
    assert "travel_to:tree_far" in offered


def test_collect_needs_a_berry_bush_on_your_own_tile() -> None:
    on_tile = build_model(
        objects=[make_object("bush_1", "bush", (10, 10), {"berry_count": "1"})]
    )
    assert "collect:bush_1" in keys(on_tile)

    empty = build_model(
        objects=[make_object("bush_1", "bush", (10, 10), {"berry_count": "0"})]
    )
    assert "collect:bush_1" not in keys(empty)

    adjacent = build_model(
        objects=[make_object("bush_1", "bush", (11, 10), {"berry_count": "1"})]
    )
    assert "collect:bush_1" not in keys(adjacent)


def test_attack_requires_an_adjacent_living_entity() -> None:
    model = build_model(
        entities=[
            make_entity("wolf_1", (11, 10), entity_type="wolf"),
            make_entity("wolf_2", (14, 10), entity_type="wolf"),
            make_entity("wolf_3", (10, 11), entity_type="wolf", alive=False),
        ]
    )
    offered = keys(model)
    assert "attack:wolf_1" in offered
    assert "attack:wolf_2" not in offered
    assert "attack:wolf_3" not in offered


def test_eat_appears_only_with_food_in_the_pack() -> None:
    assert "eat:berry" not in keys(build_model())
    with_berries = build_model(
        self_entity=make_entity("ada", (10, 10), inventory={"berry": 2})
    )
    assert "eat:berry" in keys(with_berries)


def test_craft_options_track_affordability() -> None:
    poor = build_model(self_entity=make_entity("ada", (10, 10), inventory={"wood": 1}))
    assert not [key for key in keys(poor) if key.startswith("craft:")]

    rich = build_model(
        self_entity=make_entity("ada", (10, 10), inventory={"wood": 6, "stone": 3})
    )
    offered = keys(rich)
    assert "craft:axe" in offered
    assert "craft:chest" in offered
    assert "craft:sword" in offered


def test_equip_skips_what_is_already_wielded() -> None:
    model = build_model(
        self_entity=make_entity(
            "ada", (10, 10), inventory={"axe": 1, "sword": 1}, wielded="axe"
        )
    )
    offered = keys(model)
    assert "equip:sword" in offered
    assert "equip:axe" not in offered


def test_chest_deposit_and_withdraw_reflect_both_inventories() -> None:
    model = build_model(
        self_entity=make_entity("ada", (10, 10), inventory={"wood": 3}),
        objects=[
            make_object("chest_1", "chest", (11, 10), {"contents": '{"berry": 2}'})
        ],
    )
    offered = keys(model)
    assert "deposit:chest_1:wood" in offered
    assert "withdraw:chest_1:berry" in offered
    assert "withdraw:chest_1:wood" not in offered


def test_pickup_lists_what_is_in_the_pile_underfoot() -> None:
    model = build_model(
        objects=[
            make_object(
                "pile_1", "item_pile", (10, 10), {"contents": '{"wood": 2, "stone": 1}'}
            )
        ]
    )
    offered = keys(model)
    assert "pickup:wood" in offered
    assert "pickup:stone" in offered


def test_travel_controls_appear_only_while_a_travel_is_active() -> None:
    model = build_model()
    assert "follow_travel" not in keys(model)
    assert "stop_travel" not in keys(model)

    travel = TravelState(target=(16, 10), label="the clearing")
    offered = keys(model, travel)
    assert "follow_travel" in offered
    assert "stop_travel" in offered


def test_travel_to_is_capped_at_six_candidates() -> None:
    model = build_model(
        objects=[make_object(f"tree_{i}", "tree", (10 + i, 12)) for i in range(2, 12)]
    )
    travel_keys = [key for key in keys(model) if key.startswith("travel_to:")]
    assert len(travel_keys) == 6


def test_option_list_is_capped_and_unique() -> None:
    model = build_model(
        self_entity=make_entity(
            "ada",
            (10, 10),
            inventory={"wood": 9, "stone": 9, "berry": 3, "axe": 1, "chest": 1},
        ),
        objects=[make_object(f"tree_{i}", "tree", (10 + i, 12)) for i in range(2, 14)]
        + [
            make_object("chest_1", "chest", (11, 10), {"contents": '{"berry": 2}'}),
            make_object("pile_1", "item_pile", (10, 10), {"contents": '{"stone": 1}'}),
        ],
    )
    options = enumerate_options(model)
    assert len(options) <= MAX_OPTIONS
    assert len({option.key for option in options}) == len(options)


def test_every_option_has_a_one_line_description() -> None:
    criteria = options_to_criteria(enumerate_options(build_model()))
    assert criteria
    for key, description in criteria.items():
        assert description and "\n" not in description, key


def test_canned_phrases_are_always_available() -> None:
    offered = keys(build_model())
    for phrase in SAY_PHRASES:
        assert f"say:{phrase}" in offered


def test_retreat_option_moves_away_from_the_wolf() -> None:
    model = build_model(entities=[make_entity("wolf_1", (11, 10), entity_type="wolf")])
    options = enumerate_options(model)
    retreat = retreat_option(model, options)
    assert retreat is not None
    assert retreat.key.startswith("move_")
    assert "W" in retreat.key


def test_retreat_option_is_absent_without_a_wolf() -> None:
    model = build_model()
    assert retreat_option(model, enumerate_options(model)) is None
