"""Only legal options reach Jev, and the list stays inside its budget."""

from __future__ import annotations

from typing import Sequence

from agents.jev_agent.geometry import NAME_TO_DIRECTION
from agents.jev_agent.options import (
    CRAFT_OPTION_LIMIT,
    STEP_KEY_PREFIX,
    STEP_TARGETS_PER_GROUP,
    MAX_OPTIONS,
    HEARD_SHOUT_KEY_PREFIX,
    HEARD_SHOUT_MAX_AGE_TICKS,
    MAX_BRIEF_SHOUTS,
    SAY_PHRASES,
    SHOUT_COOLDOWN_TICKS,
    Option,
    TravelState,
    enumerate_options,
    options_to_criteria,
    retreat_option,
)
from agents.jev_agent.worldmodel import WorldModel

from helpers import (
    converse_object,
    make_entity,
    make_object,
    make_observation,
    make_tiles,
    utterance_event,
)


def build_model(**observation_kwargs: object) -> WorldModel:
    """A world model for `ada` at (10, 10) after one observation."""
    model = WorldModel("ada")
    self_entity = observation_kwargs.pop("self_entity", make_entity("ada", (10, 10)))
    model.update(make_observation(1, self_entity, **observation_kwargs))  # type: ignore[arg-type]
    return model


def keys(model: WorldModel, travel: TravelState | None = None) -> list[str]:
    """Option keys for a model, in enumeration order."""
    return [option.key for option in enumerate_options(model, travel)]


def keys_with(model: WorldModel, **kwargs: object) -> list[str]:
    """Option keys with extra `enumerate_options` arguments, such as phrases."""
    return [option.key for option in enumerate_options(model, **kwargs)]  # type: ignore[arg-type]


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
    assert "step_towards:tree_far" in offered


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
    poor = build_model(self_entity=make_entity("ada", (10, 10), inventory={"berry": 1}))
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
    assert "keep_going" not in keys(model)
    assert "stop_going" not in keys(model)

    travel = TravelState(target=(16, 10), label="the clearing")
    offered = keys(model, travel)
    assert "keep_going" in offered
    assert "stop_going" in offered


def test_keep_going_names_the_target_the_step_and_the_distance() -> None:
    model = build_model()
    travel = TravelState(target=(14, 10), label="the clearing")
    criteria = options_to_criteria(enumerate_options(model, travel))
    assert criteria["keep_going"] == (
        "keep going toward the clearing (next step E, 4 steps left)"
    )


# --- step_towards: one step per tick, with a per-type quota -----------------


def step_keys(model: WorldModel, **kwargs: object) -> list[str]:
    """Just the walk options, in the order they are offered."""
    return [
        key for key in keys_with(model, **kwargs) if key.startswith(STEP_KEY_PREFIX)
    ]


def test_each_object_group_gets_its_own_two_slots() -> None:
    model = build_model(
        objects=[make_object(f"tree_{i}", "tree", (10 + i, 12)) for i in range(2, 22)]
        + [
            make_object("bush_1", "bush", (4, 10), {"berry_count": "1"}),
            make_object("rock_1", "rock_small", (3, 14)),
        ]
    )
    offered = step_keys(model)
    trees = [key for key in offered if key.startswith(f"{STEP_KEY_PREFIX}tree_")]
    assert len(trees) == STEP_TARGETS_PER_GROUP
    # A grove no longer crowds out the one bush and the one rock.
    assert f"{STEP_KEY_PREFIX}bush_1" in offered
    assert f"{STEP_KEY_PREFIX}rock_1" in offered


def test_a_step_option_describes_the_target_offset_and_distance() -> None:
    model = build_model(objects=[make_object("tree_9", "tree", (13, 8))])
    criteria = options_to_criteria(enumerate_options(model))
    assert criteria[f"{STEP_KEY_PREFIX}tree_9"] == (
        "one step toward tree_9 (tree) at dx 3 dy -2, 3 tiles away"
    )


def test_an_empty_bush_is_not_worth_walking_to() -> None:
    model = build_model(
        objects=[make_object("bush_1", "bush", (14, 10), {"berry_count": "0"})]
    )
    assert f"{STEP_KEY_PREFIX}bush_1" not in step_keys(model)


def test_every_wolf_and_the_nearest_settlers_get_a_step_option() -> None:
    model = build_model(
        entities=[
            make_entity("wolf_1", (14, 10), entity_type="wolf"),
            make_entity("wolf_2", (10, 15), entity_type="wolf"),
            make_entity("wolf_3", (6, 6), entity_type="wolf", alive=False),
            make_entity("bram", (13, 13)),
            make_entity("cleo", (7, 7)),
        ]
    )
    offered = step_keys(model)
    assert f"{STEP_KEY_PREFIX}wolf_1" in offered
    assert f"{STEP_KEY_PREFIX}wolf_2" in offered
    assert f"{STEP_KEY_PREFIX}wolf_3" not in offered, "a dead wolf is not a target"
    assert f"{STEP_KEY_PREFIX}bram" in offered
    assert f"{STEP_KEY_PREFIX}cleo" in offered


def test_an_object_the_brief_names_is_always_offered_and_comes_first() -> None:
    model = build_model(
        objects=[make_object(f"tree_{i}", "tree", (10 + i, 12)) for i in range(2, 22)]
        + [make_object("rock_77", "rock_small", (4, 4))]
    )
    offered = step_keys(model, brief_text="Mine rock_77 until it is gone.")
    assert offered[0] == f"{STEP_KEY_PREFIX}rock_77"


def test_a_brief_id_that_names_nothing_known_is_ignored() -> None:
    model = build_model()
    assert step_keys(model, brief_text="Go to tree_404.") == []


# --- named places ----------------------------------------------------------


def test_a_named_place_is_offered_with_a_relative_offset() -> None:
    model = build_model()
    criteria = options_to_criteria(
        enumerate_options(model, places={"the_lake": (14, 7)})
    )
    assert criteria[f"{STEP_KEY_PREFIX}the_lake"] == (
        "one step toward the_lake at dx 4 dy -3, 4 tiles away"
    )


def test_a_place_with_no_known_path_still_gets_a_hopeful_step() -> None:
    model = build_model()
    criteria = options_to_criteria(
        enumerate_options(model, places={"far_away": (900, 900)})
    )
    description = criteria[f"{STEP_KEY_PREFIX}far_away"]
    assert "beyond what you can see, so keep stepping" in description


def test_a_place_you_are_standing_on_is_not_offered() -> None:
    model = build_model()
    assert f"{STEP_KEY_PREFIX}here" not in keys_with(model, places={"here": (10, 10)})


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


# --- building (docs/08_building.md) ----------------------------------------


def test_reeds_and_clay_can_be_harvested_like_trees() -> None:
    model = build_model(
        objects=[
            make_object("reeds_1", "reeds", (11, 10)),
            make_object("clay_1", "clay_deposit", (10, 11)),
        ]
    )
    offered = keys(model)
    assert "extract:reeds_1" in offered
    assert "extract:clay_1" in offered


def test_reed_harvesting_states_the_bare_handed_work_rate() -> None:
    model = build_model(objects=[make_object("reeds_1", "reeds", (11, 10))])
    descriptions = options_to_criteria(enumerate_options(model))
    assert "1 work per action bare-handed" in descriptions["extract:reeds_1"]
    assert "3 work per unit" in descriptions["extract:reeds_1"]
    assert "fiber" in descriptions["extract:reeds_1"]


def test_workshop_recipes_need_a_workshop_table_nearby() -> None:
    inventory = {"plank": 4, "stone": 2, "clay": 2, "fiber": 3, "rope": 1}
    without = build_model(self_entity=make_entity("ada", (10, 10), inventory=inventory))
    offered = keys(without)
    assert "craft:stone_wall" not in offered
    assert "craft:bed" not in offered
    assert "craft:workshop_table" in offered  # a hand recipe

    with_table = build_model(
        self_entity=make_entity("ada", (10, 10), inventory=inventory),
        objects=[make_object("ws_1", "workshop_table", (11, 11))],
    )
    assert "craft:bed" in keys(with_table)


def test_craft_options_stay_inside_their_budget() -> None:
    model = build_model(
        self_entity=make_entity(
            "ada",
            (10, 10),
            inventory={"plank": 9, "stone": 9, "wood": 9, "clay": 9, "fiber": 9},
        ),
        objects=[make_object("ws_1", "workshop_table", (10, 11))],
    )
    craft_keys = [key for key in keys(model) if key.startswith("craft:")]
    assert len(craft_keys) <= CRAFT_OPTION_LIMIT
    assert craft_keys[0] == "craft:axe"


def test_a_tool_you_already_carry_is_not_offered_again() -> None:
    model = build_model(
        self_entity=make_entity(
            "ada", (10, 10), inventory={"wood": 9, "stone": 9, "axe": 1}
        )
    )
    assert "craft:axe" not in keys(model)
    assert "craft:pickaxe" in keys(model)


def test_ground_pieces_are_placed_on_your_own_tile() -> None:
    model = build_model(self_entity=make_entity("ada", (10, 10), inventory={"road": 2}))
    assert "place:road:here" in keys(model)


def test_ground_pieces_are_not_offered_over_a_natural_object() -> None:
    model = build_model(
        self_entity=make_entity("ada", (10, 10), inventory={"road": 2}),
        objects=[make_object("t1", "tree", (10, 10))],
    )
    assert "place:road:here" not in keys(model)


def test_structures_are_placed_toward_a_free_neighbour() -> None:
    model = build_model(
        self_entity=make_entity("ada", (10, 10), inventory={"wood_wall": 2})
    )
    placements = [key for key in keys(model) if key.startswith("place:wood_wall:")]
    assert len(placements) == 1
    assert not placements[0].endswith(":here")


def test_resting_is_offered_only_to_a_wounded_actor_beside_a_bed() -> None:
    bed = [make_object("bed_1", "bed", (11, 10))]
    hurt = build_model(self_entity=make_entity("ada", (10, 10), health=6), objects=bed)
    assert "rest:bed_1" in keys(hurt)

    healthy = build_model(self_entity=make_entity("ada", (10, 10)), objects=bed)
    assert "rest:bed_1" not in keys(healthy)

    far = build_model(
        self_entity=make_entity("ada", (10, 10), health=6),
        objects=[make_object("bed_1", "bed", (14, 10))],
    )
    assert "rest:bed_1" not in keys(far)


def test_jev_is_never_offered_the_chance_to_dismantle_the_town() -> None:
    model = build_model(
        objects=[
            make_object("w1", "wood_wall", (11, 10)),
            make_object("bed_1", "bed", (10, 11)),
            make_object("ws_1", "workshop_table", (9, 10)),
        ]
    )
    offered = keys(model)
    assert not [key for key in offered if key.startswith("extract:")]


# --- calling for help and answering the call ---------------------------------


def option_by_key(model: WorldModel, key: str) -> Option:
    return next(o for o in enumerate_options(model) if o.key == key)


def shout_keys(model: WorldModel, shouts: tuple[str, ...]) -> list[str]:
    return [
        option.key
        for option in enumerate_options(model, shouts=shouts)
        if option.key.startswith("shout:")
    ]


def test_jev_cannot_shout_unless_the_brief_supplies_phrases() -> None:
    wolf = make_entity("wolf_1", (14, 10), entity_type="wolf")
    assert shout_keys(build_model(entities=[wolf]), ()) == []


def test_each_brief_phrase_becomes_a_shout_option_with_or_without_a_wolf() -> None:
    phrases = ("Wolf near me!", "Come to the workshop.")
    model = build_model()
    assert shout_keys(model, phrases) == ["shout:0", "shout:1"]

    options = {o.key: o for o in enumerate_options(model, shouts=phrases)}
    shout = options["shout:1"]
    assert shout.intent.say.channel == "shout"
    assert shout.intent.say.text == "Come to the workshop."


def test_only_the_first_few_brief_phrases_are_offered() -> None:
    phrases = tuple(f"phrase {index}" for index in range(MAX_BRIEF_SHOUTS + 2))
    assert len(shout_keys(build_model(), phrases)) == MAX_BRIEF_SHOUTS


def test_shouting_has_a_cooldown() -> None:
    model = build_model(
        events=[utterance_event("ada", "Wolf!", (10, 10), channel="shout")],
    )
    assert shout_keys(model, ("Wolf!",)) == []

    ada = make_entity("ada", (10, 10))
    model.update(make_observation(1 + SHOUT_COOLDOWN_TICKS, ada))
    assert shout_keys(model, ("Wolf!",)) == ["shout:0"]


def test_a_heard_shout_offers_a_walk_to_where_it_came_from() -> None:
    model = build_model(
        events=[utterance_event("bram", "Berries here!", (16, 10), channel="shout")]
    )
    walk = option_by_key(model, f"{HEARD_SHOUT_KEY_PREFIX}bram")
    assert walk.travel_target is not None
    assert walk.travel_target.target == (16, 10)
    assert walk.travel_target.stop_adjacent
    assert "bram" in walk.description and "Berries here!" in walk.description
    assert "wolf" not in walk.description.lower()


def test_the_walk_goes_to_the_shout_origin_even_when_the_shouter_has_moved() -> None:
    model = build_model(
        entities=[make_entity("bram", (13, 12))],
        events=[utterance_event("bram", "Wolf!", (16, 10), channel="shout")],
    )
    walk = option_by_key(model, f"{HEARD_SHOUT_KEY_PREFIX}bram")
    assert walk.travel_target is not None
    assert walk.travel_target.target == (16, 10)


def test_no_walk_for_ordinary_speech_old_shouts_or_an_adjacent_origin() -> None:
    said = build_model(events=[utterance_event("bram", "hello", (16, 10))])
    assert not [k for k in keys(said) if k.startswith(HEARD_SHOUT_KEY_PREFIX)]

    stale = build_model(
        events=[utterance_event("bram", "Wolf!", (16, 10), channel="shout")]
    )
    ada = make_entity("ada", (10, 10))
    stale.update(make_observation(2 + HEARD_SHOUT_MAX_AGE_TICKS, ada))
    assert not [k for k in keys(stale) if k.startswith(HEARD_SHOUT_KEY_PREFIX)]

    beside = build_model(
        entities=[make_entity("bram", (11, 10))],
        events=[utterance_event("bram", "Wolf!", (11, 10), channel="shout")],
    )
    assert not [k for k in keys(beside) if k.startswith(HEARD_SHOUT_KEY_PREFIX)]


def test_attacking_a_wolf_says_how_many_allies_are_already_on_it() -> None:
    model = build_model(
        entities=[
            make_entity("wolf_1", (11, 10), entity_type="wolf"),
            make_entity("bram", (12, 10)),
            make_entity("cleo", (12, 11)),
            make_entity("dov", (15, 15)),
        ]
    )
    attack = option_by_key(model, "attack:wolf_1")
    assert "2 other settlers next to it" in attack.description


def test_the_walk_is_not_offered_again_while_already_walking_there() -> None:
    model = build_model(
        events=[utterance_event("bram", "Wolf!", (16, 10), channel="shout")]
    )
    walk = option_by_key(model, f"{HEARD_SHOUT_KEY_PREFIX}bram")
    assert f"{HEARD_SHOUT_KEY_PREFIX}bram" not in keys(model, walk.travel_target)


# --- stations, veins and sleep (docs/10_metal_and_sleep.md) -----------------


def test_a_furnace_recipe_needs_a_furnace_and_not_a_workshop_table() -> None:
    inventory = {"wood": 3, "copper_ore": 2, "charcoal": 1}
    at_table = build_model(
        self_entity=make_entity("ada", (10, 10), inventory=inventory),
        objects=[make_object("ws_1", "workshop_table", (11, 10))],
    )
    assert "craft:charcoal" not in keys(at_table)

    at_furnace = build_model(
        self_entity=make_entity("ada", (10, 10), inventory=inventory),
        objects=[make_object("fur_1", "furnace", (11, 10))],
    )
    offered = keys(at_furnace)
    assert "craft:charcoal" in offered
    assert "craft:copper_ingot" in offered


def test_an_anvil_recipe_needs_an_anvil() -> None:
    inventory = {"plank": 2, "iron_ingot": 3}
    at_table = build_model(
        self_entity=make_entity("ada", (10, 10), inventory=inventory),
        objects=[make_object("ws_1", "workshop_table", (11, 10))],
    )
    assert "craft:iron_sword" not in keys(at_table)

    at_anvil = build_model(
        self_entity=make_entity("ada", (10, 10), inventory=inventory),
        objects=[make_object("anv_1", "anvil", (10, 11))],
    )
    assert "craft:iron_sword" in keys(at_anvil)


def test_a_multi_action_craft_names_its_work_and_the_progress_banked() -> None:
    inventory = {"wood": 3}
    fresh = build_model(
        self_entity=make_entity("ada", (10, 10), inventory=inventory),
        objects=[make_object("fur_1", "furnace", (11, 10))],
    )
    description = options_to_criteria(enumerate_options(fresh))["craft:charcoal"]
    assert "at the furnace within reach" in description
    assert "2 craft actions, 0 done so far" in description

    started = build_model(
        self_entity=make_entity("ada", (10, 10), inventory=inventory),
        objects=[
            make_object("fur_1", "furnace", (11, 10), {"craft:ada": "charcoal:1"})
        ],
    )
    resumed = options_to_criteria(enumerate_options(started))["craft:charcoal"]
    assert "2 craft actions, 1 done so far" in resumed


def test_another_settlers_craft_progress_is_not_read_as_your_own() -> None:
    model = build_model(
        self_entity=make_entity("ada", (10, 10), inventory={"wood": 3}),
        objects=[
            make_object("fur_1", "furnace", (11, 10), {"craft:bob": "charcoal:1"})
        ],
    )
    description = options_to_criteria(enumerate_options(model))["craft:charcoal"]
    assert "2 craft actions, 0 done so far" in description


def test_a_vein_is_only_harvestable_with_a_pickaxe_of_the_right_tier() -> None:
    veins = [
        make_object("copper_1", "copper_vein", (11, 10)),
        make_object("iron_1", "iron_vein", (10, 11)),
    ]
    bare = build_model(objects=veins)
    assert "extract:copper_1" not in keys(bare)
    assert "extract:iron_1" not in keys(bare)

    stone_pick = build_model(
        self_entity=make_entity("ada", (10, 10), wielded="pickaxe"),
        objects=veins,
    )
    assert "extract:copper_1" in keys(stone_pick)
    assert "extract:iron_1" not in keys(stone_pick)

    copper_pick = build_model(
        self_entity=make_entity("ada", (10, 10), wielded="copper_pickaxe"),
        objects=veins,
    )
    assert "extract:iron_1" in keys(copper_pick)


def test_a_vein_option_states_the_work_the_wielded_tool_adds() -> None:
    model = build_model(
        self_entity=make_entity("ada", (10, 10), wielded="iron_pickaxe"),
        objects=[make_object("copper_1", "copper_vein", (11, 10))],
    )
    description = options_to_criteria(enumerate_options(model))["extract:copper_1"]
    assert "5 work per action with the iron_pickaxe" in description
    assert "copper_ore" in description
    assert "4 units left" in description


def test_the_metal_tools_can_be_wielded() -> None:
    model = build_model(
        self_entity=make_entity(
            "ada", (10, 10), inventory={"iron_sword": 1, "copper_pickaxe": 1}
        )
    )
    offered = keys(model)
    assert "equip:iron_sword" in offered
    assert "equip:copper_pickaxe" in offered


def test_sleep_is_offered_on_the_ground_and_on_an_adjacent_bed_when_tired() -> None:
    model = build_model(
        self_entity=make_entity("ada", (10, 10), fatigue=40),
        objects=[make_object("bed_1", "bed", (11, 10))],
    )
    descriptions = options_to_criteria(enumerate_options(model))
    assert "sleep:ground" in descriptions
    assert "sleep:bed_1" in descriptions
    assert "wake" not in descriptions


def test_a_fresh_settler_is_not_offered_sleep() -> None:
    model = build_model(self_entity=make_entity("ada", (10, 10), fatigue=0))
    assert "sleep:ground" not in keys(model)


def test_the_sleep_descriptions_carry_the_recovery_for_the_time_of_day() -> None:
    day = build_model(
        self_entity=make_entity("ada", (10, 10), fatigue=40),
        objects=[make_object("bed_1", "bed", (11, 10))],
    )
    by_day = options_to_criteria(enumerate_options(day))
    assert "1 fatigue per 2 ticks" in by_day["sleep:bed_1"]
    assert "1 fatigue per 4 ticks" in by_day["sleep:ground"]

    night = WorldModel("ada")
    night.update(
        make_observation(
            250,
            make_entity("ada", (10, 10), fatigue=40),
            objects=[make_object("bed_1", "bed", (11, 10))],
        )
    )
    at_night = options_to_criteria(enumerate_options(night))
    assert "1 fatigue per tick" in at_night["sleep:bed_1"]
    assert "1 fatigue per 2 ticks" in at_night["sleep:ground"]


def test_only_waking_is_offered_to_a_sleeper() -> None:
    model = build_model(
        self_entity=make_entity("ada", (10, 10), fatigue=40, asleep=True),
        objects=[make_object("bed_1", "bed", (11, 10))],
    )
    offered = keys(model)
    assert "wake" in offered
    assert "sleep:ground" not in offered
    assert "sleep:bed_1" not in offered


# -- invitations to talk (docs/09 section 8.3) -------------------------------


def invited_model(
    inviter_position: tuple[int, int] = (11, 10),
    *,
    text: str = "Come and plan the wall.",
) -> WorldModel:
    """A model for ada who heard, and can see, an open invitation from mira."""
    model = WorldModel("ada")
    model.update(
        make_observation(
            5,
            make_entity("ada", (10, 10)),
            entities=[make_entity("mira", inviter_position, open_to_talk=True)],
            events=[utterance_event("mira", text, inviter_position, open_to_talk=True)],
        )
    )
    return model


def option_for(model: WorldModel, key: str, **kwargs: object) -> Option:
    """The one option with this key; fails the test when it is not offered."""
    options = enumerate_options(model, **kwargs)  # type: ignore[arg-type]
    matches = [option for option in options if option.key == key]
    assert matches, f"{key} not offered: {[option.key for option in options]}"
    return matches[0]


def test_an_adjacent_invitation_is_the_accept_intent() -> None:
    option = option_for(invited_model((11, 10)), "talk_to:mira")

    assert option.intent.converse.action == "accept"
    assert option.intent.converse.target_entity_id == "mira"
    assert "accept mira's invitation to talk" in option.description


def test_a_distant_invitation_is_a_walk_that_stops_next_to_the_inviter() -> None:
    option = option_for(invited_model((14, 10)), "talk_to:mira")

    assert option.intent.HasField("move")
    assert option.travel_target is not None
    assert option.travel_target.target == (14, 10)
    assert option.travel_target.stop_adjacent
    assert (
        'who said "Come and plan the wall." and is open to talk' in option.description
    )


def test_nobody_open_to_talk_means_no_talk_to_option() -> None:
    model = build_model(entities=[make_entity("mira", (11, 10))])

    assert not [key for key in keys(model) if key.startswith("talk_to:")]


def test_an_invitation_phrase_is_offered_while_a_settler_is_in_earshot() -> None:
    model = build_model(entities=[make_entity("mira", (14, 10))])

    option = option_for(model, "invite:0", invitations=["Anyone want to plan?"])
    assert option.intent.say.channel == "local"
    assert option.intent.say.open_to_talk
    assert option.intent.say.text == "Anyone want to plan?"
    assert "stay open to talk for 40 ticks" in option.description


def test_an_invitation_is_not_offered_with_nobody_in_earshot() -> None:
    model = build_model(entities=[make_entity("mira", (40, 10))])

    assert "invite:0" not in keys_with(model, invitations=["Anyone want to plan?"])


def test_an_invitation_is_not_offered_while_the_actors_own_one_stands() -> None:
    model = WorldModel("ada")
    model.update(
        make_observation(
            5,
            make_entity("ada", (10, 10)),
            entities=[make_entity("mira", (12, 10))],
            events=[utterance_event("ada", "Anyone?", (10, 10), open_to_talk=True)],
        )
    )

    assert "invite:0" not in keys_with(model, invitations=["Anyone want to plan?"])


def test_neither_invitation_option_is_offered_from_a_seat() -> None:
    model = WorldModel("ada")
    model.update(
        make_observation(
            5,
            make_entity("ada", (10, 10)),
            objects=[converse_object("conv_1", (11, 11), ["mira", "ada"])],
            entities=[make_entity("mira", (11, 10), open_to_talk=True)],
            events=[utterance_event("mira", "Talk?", (11, 10), open_to_talk=True)],
        )
    )

    offered = keys_with(model, invitations=["Anyone want to plan?"])
    assert "talk_to:mira" not in offered
    assert "invite:0" not in offered
