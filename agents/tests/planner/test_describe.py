"""`look`: what the actor is shown about the world it remembers."""

from __future__ import annotations

from pydantic_ai.messages import ToolReturnPart
from agents import world_pb2 as pb
from agents.jev_agent import items, planner as planner_module
from agents.jev_agent.items import RECIPES
from agents.jev_agent.planner import (
    PlannerDeps,
    build_planner_agent,
    describe_world,
    travel_arrival,
)
from agents.jev_agent.worldmodel import WorldModel
from helpers import (
    converse_object,
    died_event,
    make_entity,
    make_object,
    make_observation,
    make_tiles,
    utterance_event,
)

from conftest import (
    RecordingBridge,
    _watch_a_wolf_die,
    bridge,
    deps,
    one_tool_call,
    world_model,
)


def test_look_lists_item_piles_with_their_contents(world_model: WorldModel) -> None:
    world_model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10)),
            objects=[
                make_object(
                    "item_pile_3",
                    "item_pile",
                    (14, 10),
                    {"contents": '{"axe": 1, "wood": 5}'},
                ),
                make_object("item_pile_4", "item_pile", (20, 10), {"contents": "{}"}),
            ],
        )
    )
    text = describe_world(world_model)
    assert "item pile item_pile_3 at (14, 10) (d4): axe x1, wood x5" in text
    assert "item_pile_4" not in text.split("item pile item_pile_3")[1]


async def test_a_failed_pickup_names_the_nearest_piles(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    bridge.model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10)),
            objects=[
                make_object(
                    "item_pile_3", "item_pile", (14, 10), {"contents": '{"axe": 1}'}
                )
            ],
        )
    )
    agent = build_planner_agent("test")
    with agent.override(model=one_tool_call("pickup", {"kind": "axe"})):
        result = await agent.run("go", deps=deps)
    returned = [
        str(part.content)
        for message in result.all_messages()
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    # The pile is four tiles off, so the refusal comes before the world sees it.
    assert "no item pile lies on the tile you stand on" in returned[0]
    assert "No tick spent." in returned[0]
    assert bridge.actions == []
    assert "item_pile_3 at (14, 10) (d4): axe x1" in returned[0]


def test_look_summary_covers_stats_objects_and_neighbours(
    world_model: WorldModel,
) -> None:
    summary = describe_world(world_model)
    assert "you are ada at (10, 10)" in summary
    assert "wood x2" in summary
    assert "tree: 1 known" in summary
    assert "chest chest_1" in summary and "berry x3" in summary
    assert "Wood pile" in summary
    assert "bob (player)" in summary


def test_look_lists_every_settler_met_even_after_they_walk_out_of_view(
    world_model: WorldModel,
) -> None:
    far_away = make_entity("ada", (40, 40), inventory={"wood": 2})
    world_model.update(make_observation(25, far_away))
    summary = describe_world(world_model)
    assert "entities in view: none" in summary
    assert "settlers you have met (last seen):" in summary
    assert "bob at (11, 10) (d30, 20 ticks ago)" in summary


def test_look_marks_an_asleep_settler_in_view_and_in_the_roster(
    world_model: WorldModel,
) -> None:
    world_model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10), inventory={"wood": 2}),
            entities=[make_entity("bob", (11, 10), asleep=True)],
        )
    )
    summary = describe_world(world_model)
    assert "bob (player) at (11, 10), hp 20/20, asleep" in summary
    assert "bob at (11, 10) (d1, now, asleep as of now)" in summary


def test_look_marks_a_dead_settler_over_asleep(world_model: WorldModel) -> None:
    world_model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10), inventory={"wood": 2}),
            entities=[make_entity("bob", (11, 10), asleep=True, alive=False)],
        )
    )
    summary = describe_world(world_model)
    assert "bob (player) at (11, 10), hp 20/20, dead" in summary
    assert "bob at (11, 10) (d1, now, dead)" in summary


def test_look_shows_where_a_shout_came_from_and_who_is_armed(
    world_model: WorldModel,
) -> None:
    world_model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10)),
            entities=[make_entity("bob", (11, 10), wielded="sword")],
            events=[utterance_event("cleo", "Wolf here!", (50, 12), channel="shout")],
        )
    )
    summary = describe_world(world_model)
    assert 'cleo SHOUTED from (50, 12), 0 ticks ago: "Wolf here!"' in summary
    assert "wielding sword" in summary


async def test_look_lists_the_conversations_in_view(world_model: WorldModel) -> None:
    world_model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10)),
            objects=[converse_object("conv_1", (11, 10), ["mira"], utterances=2)],
        )
    )
    summary = describe_world(world_model)
    assert "conversations in view:" in summary
    assert "conv_1 at (11, 10): mira" in summary
    assert "3 free seats" in summary


def test_look_names_the_veins_and_stations_it_knows(world_model: WorldModel) -> None:
    world_model.update(
        make_observation(
            7,
            make_entity("ada", (10, 10)),
            objects=[
                make_object("fur_1", "furnace", (11, 10)),
                make_object("anv_1", "anvil", (9, 10)),
                make_object("cop_1", "copper_vein", (12, 12)),
                make_object("iro_1", "iron_vein", (12, 13)),
            ],
        )
    )
    summary = describe_world(world_model)
    for line in (
        "furnace: 1 known",
        "anvil: 1 known",
        "copper_vein: 1",
        "iron_vein: 1",
    ):
        assert line in summary
    assert "day 0 7/300 day" in summary
    assert "fatigue 0/100 (fresh)" in summary


def test_travel_arrival_reads_the_tile_under_and_beside_you(
    world_model: WorldModel,
) -> None:
    assert travel_arrival(world_model, (10, 10)) == "arrived"
    assert travel_arrival(world_model, (12, 12)) == ""
    # A tile nobody can stand on counts as reached from beside it.
    world_model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10)),
            tiles=make_tiles((10, 10), blocked=[(11, 10)]),
        )
    )
    assert travel_arrival(world_model, (11, 10)) == "arrived_next_to"
    assert travel_arrival(world_model, (12, 10)) == ""


def test_the_source_text_is_generic_over_the_extraction_map() -> None:
    assert items.source_text("clay").startswith(
        "clay comes from clay_deposit (bare hands, faster with a pickaxe); "
    )
    assert items.source_text("copper_ore").startswith(
        "copper_ore comes from copper_vein (needs a pickaxe in hand); "
    )
    assert items.source_text("stone").startswith("stone comes from boulder or ")
    assert items.source_text("plank") == ""


def test_the_source_text_says_where_the_source_grows() -> None:
    """finn random-walked for 190 ticks looking for reeds he had never seen."""
    assert items.source_text("fiber") == (
        "fiber comes from reeds (bare hands); reeds grow on the banks and in "
        "the shallows of fresh water (lakes, rivers and their fords), within "
        "2 tiles of the water, and never within 12 tiles of the sea"
    )
    # Four rock types, one habitat: said once.
    assert items.source_text("stone").count("rocks and boulders gather") == 1


def test_look_lists_your_own_placed_pieces(world_model: WorldModel) -> None:
    """Journals carried goals across days but never the build site."""
    world_model.update(
        make_observation(
            7,
            make_entity("ada", (10, 10)),
            objects=[
                make_object("wood_wall_1", "wood_wall", (12, 12), {"owner": "ada"}),
                make_object("wood_wall_2", "wood_wall", (13, 12), {"owner": "ada"}),
                make_object("bed_4", "bed", (16, 16), {"owner": "ada"}),
                make_object("wood_wall_9", "wood_wall", (4, 4), {"owner": "cleo"}),
            ],
        )
    )
    text = describe_world(world_model)
    assert "your placed pieces: 2 wood_wall within (12, 12)-(13, 12)" in text
    assert "1 bed at (16, 16)" in text
    assert "wood_wall_9" not in text.split("your placed pieces:")[1].splitlines()[0]


def test_the_model_remembers_a_death_it_watched(world_model: WorldModel) -> None:
    _watch_a_wolf_die(world_model)
    death = world_model.death_of("wolf_6")
    assert death is not None
    assert death.fact() == "wolf_6 died at tick 7 (killed by esme)"
    assert [d.entity_id for d in world_model.recent_deaths()] == ["wolf_6"]


def test_a_settler_seen_alive_after_dying_is_no_longer_dead(
    world_model: WorldModel,
) -> None:
    world_model.update(
        make_observation(
            5,
            make_entity("ada", (10, 10)),
            entities=[make_entity("bram", (12, 10))],
        )
    )
    world_model.update(
        make_observation(6, make_entity("ada", (10, 10)), events=[died_event("bram")])
    )
    assert world_model.death_of("bram") is not None
    world_model.update(
        make_observation(
            20,
            make_entity("ada", (10, 10)),
            entities=[make_entity("bram", (12, 10))],
        )
    )
    assert world_model.death_of("bram") is None


def test_recent_deaths_survives_forgetting_one_while_it_iterates(
    world_model: WorldModel,
) -> None:
    """`recent_deaths` raised `deque mutated during iteration` on three turns.

    `death_of` rewrites `deaths_seen` when a body has been seen alive again,
    and `recent_deaths` calls it from inside its own loop over the deque.
    """
    _watch_a_wolf_die(world_model, tick=7)
    world_model.update(
        make_observation(
            8,
            make_entity("ada", (10, 10)),
            entities=[make_entity("bram", (12, 10))],
        )
    )
    world_model.update(
        make_observation(9, make_entity("ada", (10, 10)), events=[died_event("bram")])
    )
    # bram respawns: the next `death_of("bram")` forgets his death.
    world_model.update(
        make_observation(
            20,
            make_entity("ada", (10, 10)),
            entities=[make_entity("bram", (12, 10))],
        )
    )
    assert [d.entity_id for d in world_model.recent_deaths()] == ["wolf_6"]


def test_look_lists_the_wolves_you_saw_die(world_model: WorldModel) -> None:
    _watch_a_wolf_die(world_model)
    assert "wolves you saw die: wolf_6 (tick 7)" in describe_world(world_model)


def test_look_ages_a_wolf_that_is_only_out_of_sight(world_model: WorldModel) -> None:
    world_model.update(
        make_observation(
            5,
            make_entity("ada", (10, 10)),
            entities=[make_entity("wolf_9", (14, 10), entity_type="wolf")],
        )
    )
    world_model.update(make_observation(25, make_entity("ada", (30, 30))))
    text = describe_world(world_model)
    assert "wolves you know of but cannot see:" in text
    assert "wolf_9 last seen 20 ticks ago" in text


def _water_tiles(centre: tuple[int, int], water: tuple[int, int]) -> list[pb.Tile]:
    tiles = make_tiles(centre)
    tiles.append(
        pb.Tile(
            position=pb.Position(x=water[0], y=water[1]),
            walkable=True,
            opaque=False,
            floor_type="shallow_water",
        )
    )
    return tiles


def test_an_unseen_water_bound_source_names_the_nearest_water_you_have_seen(
    world_model: WorldModel,
) -> None:
    world_model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10)),
            tiles=_water_tiles((10, 10), (14, 12)),
        )
    )
    assert world_model.nearest_water() == (14, 12)
    text = "\n".join(
        planner_module.missing_input_lines(world_model, RECIPES[items.BED])
    )
    assert "nearest water you have seen: (14, 12) (d4)" in text
    # A material that does not grow by water gets no such line.
    plank_text = "\n".join(
        planner_module.missing_input_lines(world_model, RECIPES[items.WOOD_WALL])
    )
    assert "nearest water" not in plank_text
