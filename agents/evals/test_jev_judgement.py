"""Hand-built states that Jev should read the same way every release.

Each scenario builds the exact state and criteria a stint would send, asks the
live model, records the numbers, and only then asserts. The thresholds are
constants so that a drifting model is tuned against deliberately, in one place,
with the recorded history to argue from - never by quietly loosening the
assertion that just went red.
"""

from __future__ import annotations

import pytest

from agents.jev_agent.jevclient import JevDecision, TypeSafeJevClient
from agents.jev_agent.options import KEEP_GOING, STEP_KEY_PREFIX
from agents.jev_agent.stint import Brief
from agents.jev_agent.worldmodel import WorldModel

from .conftest import ResultsRecorder
from .scenarios import (
    make_clock,
    make_entity,
    make_object,
    make_observation,
    make_tiles,
    make_world,
    run_scenario,
)

pytestmark = pytest.mark.jev_live

# Thresholds. Generous on purpose: these catch a model that has changed its
# mind, not one that moved by 0.05. The stint ends a brief at 0.6 on two
# consecutive ticks (`stint.DONE_OR_STUCK_THRESHOLD`, `stint.LOST_THRESHOLD`),
# so "low" here means "well clear of ending the stint", not "near zero": on
# 2026-09-18 jev-latest rated a far-off named place at lost 0.36 while
# stepping toward it at 0.91 confidence, which is correct behaviour.
DONE_HIGH = 0.6
DONE_LOW = 0.3
LOST_HIGH = 0.5
LOST_LOW = 0.45
DANGER_HIGH = 0.6
STUCK_OR_LOST_HIGH = 0.5
# A raw compass step taken with this much confidence towards a target that is
# not on the map is the failure the `lost` question was added for.
BLIND_STEP_CONFIDENCE = 0.5

HOME = (100, 100)
# Every scenario runs at a tick well past the settlement's founding so the
# recent-history block is empty rather than misleadingly full.
TICK = 40
NIGHT_TICK = 250


def step_options(criteria: dict[str, str], target: str) -> set[str]:
    """Every option key that means "walk towards `target`"."""
    return {key for key in criteria if key == f"{STEP_KEY_PREFIX}{target}"}


async def ask(
    jev: TypeSafeJevClient,
    recorder: ResultsRecorder,
    request: pytest.FixtureRequest,
    model: WorldModel,
    brief: Brief,
) -> JevDecision:
    """Run one scenario against the live model and record it before asserting."""
    state, criteria = run_scenario(model, brief)
    decision = await jev.decide(state, criteria)
    recorder.record(
        request.node.nodeid,
        request.node.name,
        decision,
        thresholds={"options": len(criteria)},
    )
    return decision


async def test_eats_when_starving(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """A settler on 8 food with berries in the pack should eat one."""
    world = make_world(
        "ada",
        [
            make_observation(
                TICK,
                make_entity("ada", HOME, food=8, inventory={"berry": 3}, wielded="axe"),
                objects=[make_object("tree_1", "tree", (101, 100))],
            )
        ],
    )
    brief = Brief(
        instruction="gather wood", success_condition="you carry 5 wood", max_ticks=30
    )
    decision = await ask(jev, recorder, request, world, brief)
    top_two = [key for key, _ in decision.top(2)]
    assert "eat:berry" in top_two, f"top two were {top_two}"


async def test_done_when_inventory_met(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """Three stone in the pack against "at least 3 stone" is plainly finished."""
    world = make_world(
        "ada",
        [
            make_observation(
                TICK,
                make_entity("ada", HOME, inventory={"stone": 3}, wielded="pickaxe"),
                objects=[make_object("rock_1", "rock_medium", (102, 100))],
            )
        ],
    )
    brief = Brief(
        instruction="gather stone",
        success_condition="you are carrying at least 3 stone",
        max_ticks=30,
    )
    decision = await ask(jev, recorder, request, world, brief)
    assert decision.done > DONE_HIGH, f"done was {decision.done}"


async def test_not_done_when_inventory_short(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """One stone of three is not finished, however close it feels."""
    world = make_world(
        "ada",
        [
            make_observation(
                TICK,
                make_entity("ada", HOME, inventory={"stone": 1}, wielded="pickaxe"),
                objects=[make_object("rock_1", "rock_medium", (102, 100))],
            )
        ],
    )
    brief = Brief(
        instruction="gather stone",
        success_condition="you are carrying at least 3 stone",
        max_ticks=30,
    )
    decision = await ask(jev, recorder, request, world, brief)
    assert decision.done < DONE_LOW, f"done was {decision.done}"


async def test_lost_when_target_out_of_view(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """No river, no reeds, no step option towards either: the brief is unreachable.

    This is the recorded failure the `lost` question was written for - Jev used
    to step north and south for thirty ticks at stuck 0.4.
    """
    world = make_world(
        "ada",
        [
            make_observation(
                TICK,
                make_entity("ada", HOME, wielded="axe"),
                tiles=make_tiles(HOME),
                objects=[
                    make_object("tree_1", "tree", (104, 97)),
                    make_object("tree_2", "tree", (96, 103)),
                ],
            )
        ],
    )
    brief = Brief(
        instruction="Walk to the river and collect fiber from reeds",
        success_condition="you are carrying 2 fiber",
        max_ticks=40,
    )
    decision = await ask(jev, recorder, request, world, brief)
    assert not (
        decision.action.startswith("move_")
        and decision.confidence > BLIND_STEP_CONFIDENCE
    ), f"stepped blindly: {decision.action} at {decision.confidence}"
    assert decision.lost > LOST_HIGH, f"lost was {decision.lost}"


async def test_not_lost_when_target_visible(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """The same brief with reeds five tiles away is ordinary work, not lost."""
    reeds = (105, 100)
    tiles = make_tiles(HOME)
    world = make_world(
        "ada",
        [
            make_observation(
                TICK,
                make_entity("ada", HOME, wielded="axe"),
                tiles=tiles,
                objects=[make_object("reeds_1", "reeds", reeds)],
            )
        ],
    )
    brief = Brief(
        instruction="Walk to the river and collect fiber from reeds",
        success_condition="you are carrying 2 fiber",
        max_ticks=40,
    )
    state, criteria = run_scenario(world, brief)
    decision = await jev.decide(state, criteria)
    towards_reeds = step_options(criteria, "reeds_1")
    recorder.record(
        request.node.nodeid,
        request.node.name,
        decision,
        thresholds={"options": len(criteria), "towards_reeds": sorted(towards_reeds)},
    )
    assert towards_reeds, f"no step option towards the reeds in {sorted(criteria)}"
    assert decision.lost < LOST_LOW, f"lost was {decision.lost}"
    assert decision.action in towards_reeds | {KEEP_GOING}, f"chose {decision.action}"


async def test_attacks_adjacent_wolf_with_ally(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """Armed, healthy, with an ally already on the wolf: hit it."""
    wolf = (101, 100)
    world = make_world(
        "ada",
        [
            make_observation(
                TICK,
                make_entity("ada", HOME, wielded="sword"),
                entities=[
                    make_entity(
                        "wolf_1", wolf, entity_type="wolf", health=16, max_health=16
                    ),
                    make_entity("bram", (102, 100), wielded="sword"),
                ],
            )
        ],
    )
    brief = Brief(
        instruction="fight the wolf with the others",
        success_condition="the wolf is dead",
        max_ticks=20,
    )
    decision = await ask(jev, recorder, request, world, brief)
    assert decision.action == "attack:wolf_1", f"chose {decision.action}"


async def test_danger_when_low_health_and_wolf(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """Three health, a wolf biting, nobody else: that is dying."""
    world = make_world(
        "ada",
        [
            make_observation(
                TICK,
                make_entity("ada", HOME, health=3, wielded="sword"),
                entities=[
                    make_entity(
                        "wolf_1",
                        (101, 100),
                        entity_type="wolf",
                        health=16,
                        max_health=16,
                    )
                ],
            )
        ],
    )
    brief = Brief(
        instruction="gather wood", success_condition="you carry 5 wood", max_ticks=30
    )
    decision = await ask(jev, recorder, request, world, brief)
    assert decision.danger > DANGER_HIGH, f"danger was {decision.danger}"


@pytest.mark.xfail(
    reason=(
        "jev-latest rates a missing station at stuck 0.33 / lost 0.49 "
        "(2026-09-18): below the 0.6 gate, so such a brief still runs to its "
        "budget. Kept as a marker for the next wording or model change."
    ),
    strict=False,
)
async def test_stuck_when_recipe_needs_station(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """A copper pickaxe needs a workshop table, and there is none anywhere."""
    world = make_world(
        "ada",
        [
            make_observation(
                TICK,
                make_entity(
                    "ada",
                    HOME,
                    inventory={"plank": 2, "copper_ingot": 2},
                    wielded="axe",
                ),
            )
        ],
    )
    brief = Brief(
        instruction="craft a copper_pickaxe here",
        success_condition="you are carrying a copper_pickaxe",
        max_ticks=20,
    )
    decision = await ask(jev, recorder, request, world, brief)
    blocked = max(decision.stuck, decision.lost)
    assert blocked > STUCK_OR_LOST_HIGH, f"stuck {decision.stuck}, lost {decision.lost}"


async def test_waits_or_sleeps_when_told_to_rest_at_night(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """Night, fatigue 70, a bed one tile away, and a brief that says sleep on it."""
    world = make_world(
        "ada",
        [
            make_observation(
                NIGHT_TICK,
                make_entity("ada", HOME, fatigue=70),
                objects=[make_object("bed_1", "bed", (101, 100))],
                clock=make_clock(NIGHT_TICK),
            )
        ],
    )
    brief = Brief(
        instruction="sleep on the bed until rested",
        success_condition="your fatigue is 0",
        max_ticks=120,
    )
    decision = await ask(jev, recorder, request, world, brief)
    assert decision.action == "sleep:bed_1", f"chose {decision.action}"


async def test_steps_toward_named_bush_through_a_grove(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """The recorded starvation: food 0, a berry bush six tiles off, trees all round.

    The brief names the bush by id, so a step option toward it must exist even
    though every nearer object is a tree, and Jev should take it.
    """
    bush = (HOME[0], HOME[1] + 6)
    trees = [
        make_object(f"tree_{index}", "tree", (HOME[0] + dx, HOME[1] + dy))
        for index, (dx, dy) in enumerate(
            [
                (0, -2),
                (1, -2),
                (2, 1),
                (1, 2),
                (0, -3),
                (2, -3),
                (3, -3),
                (3, 0),
                (-1, 3),
                (4, -4),
                (-4, 1),
                (4, 1),
                (-1, 4),
                (-5, -5),
                (1, -5),
                (-5, -3),
                (-3, 5),
            ]
        )
    ]
    world = make_world(
        "dov",
        [
            make_observation(
                TICK,
                make_entity("dov", HOME, food=0, health=12, inventory={"wood": 9}),
                tiles=make_tiles(HOME),
                objects=trees
                + [make_object("bush_1", "bush", bush, {"berry_count": "1"})],
            )
        ],
    )
    brief = Brief(
        instruction="Pick berries from bush_1 and eat them until your food is above 40.",
        success_condition="your food is above 40",
        max_ticks=40,
        notes="You are at food 0 and losing health.",
    )
    state, criteria = run_scenario(world, brief)
    decision = await jev.decide(state, criteria)
    towards_bush = step_options(criteria, "bush_1")
    recorder.record(
        request.node.nodeid,
        request.node.name,
        decision,
        thresholds={"options": len(criteria), "towards_bush": sorted(towards_bush)},
    )
    assert towards_bush, f"no step option towards the bush in {sorted(criteria)}"
    assert decision.action in towards_bush, f"chose {decision.action}"
    assert decision.lost < LOST_LOW, f"lost was {decision.lost}"


async def test_steps_toward_a_named_place_out_of_view(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """A river forty tiles off is unreachable unnamed, ordinary work once named."""
    world = make_world(
        "ada",
        [
            make_observation(
                TICK,
                make_entity("ada", HOME),
                tiles=make_tiles(HOME),
                objects=[make_object("tree_1", "tree", (104, 97))],
            )
        ],
    )
    brief = Brief(
        instruction="Step toward the river until you can see reeds, then gather fiber.",
        success_condition="you are carrying 2 fiber",
        max_ticks=80,
        places={"river": (HOME[0] - 40, HOME[1] + 10)},
    )
    state, criteria = run_scenario(world, brief)
    decision = await jev.decide(state, criteria)
    towards_river = step_options(criteria, "river")
    recorder.record(
        request.node.nodeid,
        request.node.name,
        decision,
        thresholds={"options": len(criteria), "towards_river": sorted(towards_river)},
    )
    assert towards_river, f"no step option towards the river in {sorted(criteria)}"
    assert decision.action in towards_river, f"chose {decision.action}"
    assert decision.lost < LOST_LOW, f"lost was {decision.lost}"
