"""Basic competence: one obvious right move, and Jev should take it.

Each scenario shows Jev a small, unambiguous situation - a tree to the east
and a brief that says go to the tree - and asserts the plainly correct option
comes top. These are the floor under `test_jev_judgement.py`: a model that
fails here has not drifted at the margins, it has stopped reading the state.
"""

from __future__ import annotations

import pytest

from agents.jev_agent.jevclient import JevDecision, TypeSafeJevClient
from agents.jev_agent.options import STEP_KEY_PREFIX
from agents.jev_agent.stint import Brief
from agents.jev_agent.worldmodel import WorldModel

from .conftest import ResultsRecorder
from .scenarios import (
    make_entity,
    make_object,
    make_observation,
    make_tiles,
    make_world,
    run_scenario,
)

pytestmark = pytest.mark.jev_live

HOME = (100, 100)
TICK = 40


async def decide(
    jev: TypeSafeJevClient,
    recorder: ResultsRecorder,
    request: pytest.FixtureRequest,
    model: WorldModel,
    brief: Brief,
    *,
    expected: set[str],
) -> tuple[JevDecision, set[str]]:
    """Ask the live model and record the answer against the options expected."""
    state, criteria = run_scenario(model, brief)
    offered = expected & set(criteria)
    decision = await jev.decide(state, criteria)
    recorder.record(
        request.node.nodeid,
        request.node.name,
        decision,
        thresholds={"options": len(criteria), "expected": sorted(offered)},
    )
    assert offered, f"none of {sorted(expected)} was offered in {sorted(criteria)}"
    return decision, offered


def one_scene(
    entity: object, objects: list[object], entities: list[object] = []
) -> WorldModel:
    """A world seen once: open grass around home with these things in it."""
    return make_world(
        "ada",
        [
            make_observation(
                TICK,
                entity,  # type: ignore[arg-type]
                tiles=make_tiles(HOME),
                objects=objects,  # type: ignore[arg-type]
                entities=entities,  # type: ignore[arg-type]
            )
        ],
    )


async def test_walks_east_to_the_tree_it_was_sent_to(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """A tree four tiles east and "go to tree_1": step toward it, or east."""
    world = one_scene(
        make_entity("ada", HOME), [make_object("tree_1", "tree", (104, 100))]
    )
    brief = Brief(
        instruction="Walk to tree_1 and chop it for wood.",
        success_condition="you are carrying 2 wood",
        max_ticks=30,
    )
    decision, offered = await decide(
        jev,
        recorder,
        request,
        world,
        brief,
        expected={f"{STEP_KEY_PREFIX}tree_1", "move_E"},
    )
    assert decision.action in offered, f"chose {decision.action}"


async def test_walks_toward_the_named_object_not_the_nearer_one(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """A tree two tiles east, a rock five tiles west, and the brief names the rock."""
    world = one_scene(
        make_entity("ada", HOME, wielded="pickaxe"),
        [
            make_object("tree_1", "tree", (102, 100)),
            make_object("rock_1", "rock_medium", (95, 100), {"remaining": "4"}),
        ],
    )
    brief = Brief(
        instruction="Walk to rock_1 and mine it for stone.",
        success_condition="you are carrying 2 stone",
        max_ticks=30,
    )
    decision, offered = await decide(
        jev,
        recorder,
        request,
        world,
        brief,
        expected={f"{STEP_KEY_PREFIX}rock_1", "move_W"},
    )
    assert decision.action in offered, f"chose {decision.action}"
    assert decision.probabilities.get(f"{STEP_KEY_PREFIX}tree_1", 0) < 0.3


async def test_chops_the_tree_it_is_standing_next_to(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    world = one_scene(
        make_entity("ada", HOME, wielded="axe", inventory={"axe": 1}),
        [make_object("tree_1", "tree", (101, 100), {"remaining": "4"})],
    )
    brief = Brief(
        instruction="Chop tree_1 for wood.",
        success_condition="you are carrying 3 wood",
        max_ticks=30,
    )
    decision, _ = await decide(
        jev, recorder, request, world, brief, expected={"extract:tree_1"}
    )
    assert decision.action == "extract:tree_1", f"chose {decision.action}"


async def test_crafts_the_axe_it_was_told_to_craft(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    world = one_scene(
        make_entity("ada", HOME, inventory={"wood": 2, "stone": 1}),
        [make_object("tree_1", "tree", (103, 100))],
    )
    brief = Brief(
        instruction="Craft an axe from the wood and stone you carry.",
        success_condition="you are carrying an axe",
        max_ticks=10,
    )
    decision, _ = await decide(
        jev, recorder, request, world, brief, expected={"craft:axe"}
    )
    assert decision.action == "craft:axe", f"chose {decision.action}"


async def test_picks_the_berry_off_the_bush_it_stands_on(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    world = one_scene(
        make_entity("ada", HOME, food=50),
        [make_object("bush_1", "bush", HOME, {"berry_count": "1"})],
    )
    brief = Brief(
        instruction="Pick berries from bush_1.",
        success_condition="you are carrying 1 berry",
        max_ticks=10,
    )
    decision, _ = await decide(
        jev, recorder, request, world, brief, expected={"collect:bush_1"}
    )
    assert decision.action == "collect:bush_1", f"chose {decision.action}"


async def test_takes_the_axe_from_the_pile_it_stands_on(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    world = one_scene(
        make_entity("ada", HOME),
        [
            make_object(
                "item_pile_1", "item_pile", HOME, {"contents": '{"axe": 1, "wood": 4}'}
            )
        ],
    )
    brief = Brief(
        instruction="Pick up the axe from the pile you are standing on.",
        success_condition="you are carrying an axe",
        max_ticks=10,
    )
    decision, _ = await decide(
        jev, recorder, request, world, brief, expected={"pickup:axe"}
    )
    assert decision.action == "pickup:axe", f"chose {decision.action}"


async def test_equips_the_axe_before_chopping(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """Carrying an axe, wielding nothing, next to a tree: wield it first."""
    world = one_scene(
        make_entity("ada", HOME, inventory={"axe": 1}),
        [make_object("tree_1", "tree", (101, 100), {"remaining": "4"})],
    )
    brief = Brief(
        instruction="Equip your axe, then chop tree_1 for wood.",
        success_condition="you are carrying 3 wood",
        max_ticks=30,
    )
    decision, _ = await decide(
        jev, recorder, request, world, brief, expected={"equip:axe"}
    )
    assert decision.action == "equip:axe", f"chose {decision.action}"


async def test_deposits_into_the_chest_beside_it(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    world = one_scene(
        make_entity("ada", HOME, inventory={"wood": 6}),
        [make_object("chest_1", "chest", (101, 100), {"contents": "{}"})],
    )
    brief = Brief(
        instruction="Put all your wood into chest_1.",
        success_condition="you are carrying no wood",
        max_ticks=10,
    )
    decision, _ = await decide(
        jev, recorder, request, world, brief, expected={"deposit:chest_1:wood"}
    )
    assert decision.action == "deposit:chest_1:wood", f"chose {decision.action}"


async def test_places_the_wall_it_carries(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    world = one_scene(make_entity("ada", HOME, inventory={"wood_wall": 2}), [])
    brief = Brief(
        instruction="Place a wood_wall on the tile next to you.",
        success_condition="a wood_wall stands next to you",
        max_ticks=10,
    )
    state, criteria = run_scenario(world, brief)
    placements = {key for key in criteria if key.startswith("place:wood_wall:")}
    decision = await jev.decide(state, criteria)
    recorder.record(
        request.node.nodeid,
        request.node.name,
        decision,
        thresholds={"options": len(criteria), "expected": sorted(placements)},
    )
    assert placements, f"no placement offered in {sorted(criteria)}"
    assert decision.action in placements, f"chose {decision.action}"


async def test_attacks_the_wolf_it_was_told_to_fight(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    world = one_scene(
        make_entity("ada", HOME, wielded="sword", inventory={"sword": 1}),
        [],
        [
            make_entity(
                "wolf_1", (101, 100), entity_type="wolf", health=16, max_health=16
            )
        ],
    )
    brief = Brief(
        instruction="Attack wolf_1 until it is dead.",
        success_condition="wolf_1 is dead",
        max_ticks=30,
    )
    decision, _ = await decide(
        jev, recorder, request, world, brief, expected={"attack:wolf_1"}
    )
    assert decision.action == "attack:wolf_1", f"chose {decision.action}"


async def test_eats_when_told_to_eat(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    world = one_scene(make_entity("ada", HOME, food=45, inventory={"berry": 2}), [])
    brief = Brief(
        instruction="Eat a berry.",
        success_condition="your food is above 60",
        max_ticks=5,
    )
    decision, _ = await decide(
        jev, recorder, request, world, brief, expected={"eat:berry"}
    )
    assert decision.action == "eat:berry", f"chose {decision.action}"
