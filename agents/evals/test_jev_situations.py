"""Harder situations: the per-tick judgements a settler actually has to make.

`test_jev_basics.py` asks whether the model reads the state at all and
`test_jev_judgement.py` pins the four nouls on clean cases. This file is the
interesting middle: a legal option that would be a mistake, a success condition
that does not mean what it looks like, a wolf that is close but not a problem,
a shout that the brief does or does not licence. Every scenario is built to be
answerable from the state alone, so the same states can be put to an ordinary
chat model on OpenRouter and the two compared line for line.

Thresholds are the constants from `test_jev_judgement.py`, restated here with
the same values. They are generous on purpose: a scenario that goes red is
evidence, not a threshold to loosen.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import pytest

from agents import world_pb2 as pb
from agents.jev_agent.jevclient import JevDecision, TypeSafeJevClient
from agents.jev_agent.jevstate import StintProgress
from agents.jev_agent.options import (
    HEARD_SHOUT_KEY_PREFIX,
    KEEP_GOING,
    STEP_KEY_PREFIX,
    STOP_GOING,
    BriefHail,
    TravelState,
)
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

# Kept identical to `test_jev_judgement.py`: the stint ends a brief at 0.6 on
# two consecutive ticks, so "low" means "well clear of ending the stint".
DONE_HIGH = 0.6
DONE_LOW = 0.3
LOST_HIGH = 0.5
LOST_LOW = 0.45
DANGER_HIGH = 0.6
DANGER_LOW = 0.5
STUCK_OR_LOST_HIGH = 0.5

HOME = (100, 100)
TICK = 40
NIGHT_TICK = 250

# `utterance_event` and `damaged_event` live with the other builders; the evals
# reach them the same way `scenarios.py` does.
from helpers import (  # type: ignore[import-not-found]  # noqa: E402
    utterance_event,
)


def step_to(target: str) -> str:
    """The option key that means "walk towards `target`"."""
    return f"{STEP_KEY_PREFIX}{target}"


async def ask(
    jev: TypeSafeJevClient,
    recorder: ResultsRecorder,
    request: pytest.FixtureRequest,
    model: WorldModel,
    brief: Brief,
    *,
    expected: str,
    progress: StintProgress | None = None,
    extra: Mapping[str, Any] = {},
) -> tuple[JevDecision, dict[str, str]]:
    """Run one scenario against the live model and record it before asserting.

    `expected` is the gold answer in words; it goes into the results row so the
    Jev-vs-chat-model matrix can print what the right answer was.
    """
    state, criteria = run_scenario(model, brief, progress=progress)
    decision = await jev.decide(state, criteria)
    recorder.record(
        request.node.nodeid,
        request.node.name,
        decision,
        thresholds={"options": len(criteria), "expected": expected, **extra},
    )
    return decision, criteria


def top_keys(decision: JevDecision, count: int = 2) -> list[str]:
    """The `count` highest-scoring option keys."""
    return [key for key, _ in decision.top(count)]


def scene(
    entity_id: str,
    self_entity: pb.Entity,
    *,
    tiles: Sequence[pb.Tile] | None = None,
    objects: Sequence[pb.WorldObject] = (),
    entities: Sequence[pb.Entity] = (),
    events: Sequence[pb.ObservationEvent] = (),
    tick: int = TICK,
    clock: pb.WorldClock | None = None,
) -> WorldModel:
    """A world seen once, at `tick`."""
    return make_world(
        entity_id,
        [
            make_observation(
                tick,
                self_entity,
                tiles=make_tiles(HOME) if tiles is None else tiles,
                objects=objects,
                entities=entities,
                events=events,
                clock=clock,
            )
        ],
    )


def boxed_tiles(centre: tuple[int, int], radius: int = 8) -> list[pb.Tile]:
    """Known ground with an impassable ridge all round its edge.

    Without a closed ring the path finder simply walks off into the unknown,
    which it treats as passable; this is how a scenario says "there is no way
    through from here".
    """
    edge = {
        (centre[0] + dx, centre[1] + dy)
        for dx in range(-radius, radius + 1)
        for dy in range(-radius, radius + 1)
        if max(abs(dx), abs(dy)) == radius
    }
    return make_tiles(centre, radius, blocked=edge)


# --- Temptation and restraint ----------------------------------------------


async def test_does_not_attack_the_settler_standing_beside_it(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """ "Fight anything that threatens you" with a neighbour in range: the
    neighbour is a legal `attack:` option, and hitting them would be a disaster."""
    world = scene(
        "ada",
        make_entity("ada", HOME, wielded="sword"),
        entities=[
            make_entity("bram", (101, 100), wielded="axe"),
            make_entity("wolf_1", (106, 100), entity_type="wolf", health=16),
        ],
    )
    brief = Brief(
        instruction="Fight anything that threatens you.",
        success_condition="no wolf is within 10 tiles of you",
        max_ticks=20,
    )
    decision, criteria = await ask(
        jev,
        recorder,
        request,
        world,
        brief,
        expected="anything but attack:bram (step toward or wait for the wolf)",
        extra={"forbidden": "attack:bram"},
    )
    assert "attack:bram" in criteria, "the scenario needs the temptation to exist"
    assert "attack:bram" not in top_keys(decision), f"top two {top_keys(decision)}"


async def test_chops_the_tree_rather_than_picking_the_berry_underfoot(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """Well fed, told to gather wood, standing on a bush with a berry: the
    berry is free and legal and still the wrong thing to spend the tick on."""
    world = scene(
        "ada",
        make_entity("ada", HOME, food=80, wielded="axe"),
        objects=[
            make_object("tree_1", "tree", (101, 100)),
            make_object("bush_1", "bush", HOME, {"berry_count": "1"}),
        ],
    )
    brief = Brief(
        instruction="Chop tree_1 for wood.",
        success_condition="you are carrying 4 wood",
        max_ticks=30,
    )
    decision, criteria = await ask(
        jev,
        recorder,
        request,
        world,
        brief,
        expected="extract:tree_1",
        extra={"forbidden": "collect:bush_1"},
    )
    assert "collect:bush_1" in criteria, "the scenario needs the temptation to exist"
    assert "extract:tree_1" in top_keys(decision), f"top two {top_keys(decision)}"


async def test_keeps_working_with_a_wolf_seven_tiles_off(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """Full health, reeds underfoot, a wolf across the clearing: a wolf in view
    is not yet a wolf on you, and abandoning every stint that sees one is how
    nothing gets built."""
    world = scene(
        "ada",
        make_entity("ada", HOME, health=20),
        objects=[make_object("reeds_1", "reeds", (101, 100))],
        entities=[make_entity("wolf_1", (107, 100), entity_type="wolf", health=16)],
    )
    brief = Brief(
        instruction="Gather fiber from reeds_1.",
        success_condition="you are carrying 3 fiber",
        max_ticks=30,
    )
    decision, _ = await ask(
        jev,
        recorder,
        request,
        world,
        brief,
        expected="extract:reeds_1, danger below 0.5",
    )
    assert "extract:reeds_1" in top_keys(decision), f"top two {top_keys(decision)}"
    assert decision.danger < DANGER_LOW, f"danger was {decision.danger}"


# --- Reading the state, not the surface -------------------------------------


async def test_planks_do_not_satisfy_a_wood_count(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """Five planks against "carry 5 wood": the right number, the wrong item, and
    the planks were made out of the wood so there is none left."""
    world = scene(
        "ada",
        make_entity("ada", HOME, inventory={"plank": 5}, wielded="axe"),
        objects=[make_object("tree_1", "tree", (101, 100))],
    )
    brief = Brief(
        instruction="Gather wood from the trees here.",
        success_condition="you are carrying 5 wood",
        max_ticks=30,
    )
    decision, _ = await ask(
        jev, recorder, request, world, brief, expected="done below 0.3"
    )
    assert decision.done < DONE_LOW, f"done was {decision.done}"


@pytest.mark.xfail(
    reason=(
        "jev-latest rates 'standing next to chest_1' while one tile from it "
        "at done 0.16-0.23 over two runs (2026-09-19) and steps east instead: "
        "a positional success condition is read as unmet. Kept as recorded "
        "evidence - a settler sent to a chest would walk onto it and beyond."
    ),
    strict=False,
)
async def test_done_when_the_condition_is_where_you_stand(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """A success condition about position, not inventory: the chest is one tile
    away, which is what "next to" means, and the pack is irrelevant."""
    world = scene(
        "ada",
        make_entity("ada", HOME, inventory={}),
        objects=[
            make_object("chest_1", "chest", (101, 100), {"contents": '{"wood": 4}'})
        ],
    )
    brief = Brief(
        instruction="Walk to chest_1.",
        success_condition="you are standing next to chest_1",
        max_ticks=40,
    )
    decision, _ = await ask(
        jev, recorder, request, world, brief, expected="done above 0.6"
    )
    assert decision.done > DONE_HIGH, f"done was {decision.done}"


async def test_done_when_the_wall_is_standing(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """The wall went up last tick: it is on the map, the pack is empty, and the
    brief is finished even though nothing was gathered."""
    world = scene(
        "ada",
        make_entity("ada", HOME, inventory={}),
        objects=[make_object("wood_wall_1", "wood_wall", (101, 100))],
    )
    brief = Brief(
        instruction="Place a wood_wall next to you.",
        success_condition="a wood_wall stands on a tile next to you",
        max_ticks=20,
    )
    decision, _ = await ask(
        jev, recorder, request, world, brief, expected="done above 0.6"
    )
    assert decision.done > DONE_HIGH, f"done was {decision.done}"


async def test_forty_ticks_of_stepping_nowhere_is_blocked(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """`so_far` says forty steps and no net movement, and `travel.next_step` says
    blocked: the state contains the whole failure and nothing here will fix it."""
    world = scene(
        "ada",
        make_entity("ada", HOME),
        tiles=boxed_tiles(HOME),
        objects=[make_object("tree_1", "tree", (103, 97))],
    )
    brief = Brief(
        instruction="Walk to the reeds on the far bank and gather fiber.",
        success_condition="you are carrying 3 fiber",
        max_ticks=60,
        travel=TravelState(target=(HOME[0], HOME[1] + 15), label="the far bank"),
    )
    progress = StintProgress(
        ticks_used=40,
        ticks_left=20,
        inventory_change={},
        actions={"step_towards": 28, "move": 12},
        moved_from_start=(0, 0),
        net_tiles_moved=0,
    )
    decision, _ = await ask(
        jev,
        recorder,
        request,
        world,
        brief,
        expected="max(stuck, lost) above 0.5",
        progress=progress,
    )
    blocked = max(decision.stuck, decision.lost)
    assert blocked > STUCK_OR_LOST_HIGH, f"stuck {decision.stuck}, lost {decision.lost}"


async def test_gathers_the_missing_fiber_instead_of_crafting(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """Rope costs 2 fiber and the pack holds 1, so `craft:rope` is not even
    offered: the right read is "one more harvest", not "the brief is broken"."""
    world = scene(
        "ada",
        make_entity("ada", HOME, inventory={"fiber": 1}),
        objects=[make_object("reeds_1", "reeds", (101, 100))],
    )
    brief = Brief(
        instruction="Craft a rope.",
        success_condition="you are carrying a rope",
        max_ticks=30,
        notes="Rope costs 2 fiber. reeds_1 is next to you.",
    )
    decision, criteria = await ask(
        jev,
        recorder,
        request,
        world,
        brief,
        expected="extract:reeds_1, lost below 0.45",
    )
    assert "craft:rope" not in criteria, "one fiber should not buy a rope"
    assert "extract:reeds_1" in top_keys(decision), f"top two {top_keys(decision)}"
    assert decision.lost < LOST_LOW, f"lost was {decision.lost}"


@pytest.mark.xfail(
    reason=(
        "jev-latest rates a vein it has no extract option for at stuck 0.13 / "
        "lost 0.04 (2026-09-19, both runs) and walks east: the missing option "
        "is not read as 'nothing here can do this'. The tool tiers are not in "
        "`facts`, so this is as much a state question as a model one."
    ),
    strict=False,
)
async def test_iron_vein_needs_a_better_pickaxe(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """Standing on iron with a stone pickaxe: no extract option is offered and
    nothing within reach changes that, so the brief needs the planner back."""
    world = scene(
        "ada",
        make_entity("ada", HOME, wielded="pickaxe"),
        objects=[make_object("iron_vein_1", "iron_vein", (101, 100))],
    )
    brief = Brief(
        instruction="Mine iron ore from iron_vein_1.",
        success_condition="you are carrying 2 iron_ore",
        max_ticks=30,
    )
    decision, criteria = await ask(
        jev,
        recorder,
        request,
        world,
        brief,
        expected="max(stuck, lost) above 0.5",
    )
    assert "extract:iron_vein_1" not in criteria, "a stone pickaxe cannot bite iron"
    blocked = max(decision.stuck, decision.lost)
    assert blocked > STUCK_OR_LOST_HIGH, f"stuck {decision.stuck}, lost {decision.lost}"


# --- Survival judgement -----------------------------------------------------


async def test_goes_for_food_at_zero_with_no_berries_in_the_pack(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """Food 0 and health 6 with a berry bush four tiles away: the stone brief is
    not worth dying for, and there is no `eat:` option to take the easy way."""
    world = scene(
        "dov",
        make_entity("dov", HOME, food=0, health=6, wielded="pickaxe"),
        objects=[
            make_object("rock_1", "rock_medium", (101, 100)),
            make_object("bush_1", "bush", (104, 100), {"berry_count": "1"}),
        ],
    )
    brief = Brief(
        instruction="Gather stone from the rocks here.",
        success_condition="you are carrying 4 stone",
        max_ticks=40,
    )
    decision, criteria = await ask(
        jev,
        recorder,
        request,
        world,
        brief,
        expected=step_to("bush_1"),
    )
    assert step_to("bush_1") in criteria, f"no walk to the bush in {sorted(criteria)}"
    assert step_to("bush_1") in top_keys(decision), f"top two {top_keys(decision)}"


async def test_sleeps_on_the_ground_before_collapsing(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """Fatigue 97 at midday: three more ticks of work and the body falls over
    where it stands. Sleeping badly now beats collapsing in the open."""
    world = scene(
        "ada",
        make_entity("ada", HOME, fatigue=97, wielded="axe"),
        objects=[make_object("tree_1", "tree", (101, 100))],
        clock=make_clock(TICK),
    )
    brief = Brief(
        instruction="Chop tree_1 for wood.",
        success_condition="you are carrying 6 wood",
        max_ticks=40,
    )
    decision, criteria = await ask(
        jev,
        recorder,
        request,
        world,
        brief,
        expected="sleep:ground in the top two, or stuck above 0.5",
    )
    assert "sleep:ground" in criteria, "ground sleep should be offered above fatigue 0"
    assert (
        "sleep:ground" in top_keys(decision) or decision.stuck > STUCK_OR_LOST_HIGH
    ), f"top two {top_keys(decision)}, stuck {decision.stuck}"


@pytest.mark.xfail(
    reason=(
        "jev-latest rates two adjacent wolves against 14 health and no ally at "
        "danger 0.48 (2026-09-19, both runs), below the 0.6 the judgement "
        "suite uses for a dying settler - though it does end the stint via "
        "lost 0.78. Kept: six damage a tick is three ticks from death."
    ),
    strict=False,
)
async def test_two_wolves_adjacent_and_alone_is_danger(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """Six damage a tick against 14 health and no ally: three ticks to live, and
    the sword kills neither wolf in that time."""
    world = scene(
        "ada",
        make_entity("ada", HOME, health=14, wielded="sword"),
        entities=[
            make_entity("wolf_1", (101, 100), entity_type="wolf", health=16),
            make_entity("wolf_2", (99, 100), entity_type="wolf", health=16),
        ],
    )
    brief = Brief(
        instruction="Gather wood from the trees near the site.",
        success_condition="you are carrying 6 wood",
        max_ticks=40,
        shouts=("Wolves on me, help!",),
    )
    decision, _ = await ask(
        jev, recorder, request, world, brief, expected="danger above 0.6"
    )
    assert decision.danger > DANGER_HIGH, f"danger was {decision.danger}"


async def test_low_health_with_an_ally_on_the_wolf(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """Five health, one wolf on you, one armed settler already fighting it: the
    honest answers are hit it or back off - carrying on chopping is not one."""
    world = scene(
        "ada",
        make_entity("ada", HOME, health=5, wielded="sword"),
        objects=[make_object("tree_1", "tree", (100, 101))],
        entities=[
            make_entity("wolf_1", (101, 100), entity_type="wolf", health=10),
            make_entity("bram", (102, 100), wielded="sword"),
        ],
    )
    brief = Brief(
        instruction="Chop tree_1 for wood.",
        success_condition="you are carrying 6 wood",
        max_ticks=40,
    )
    decision, _ = await ask(
        jev,
        recorder,
        request,
        world,
        brief,
        expected="attack:wolf_1 or a move away; never extract:tree_1",
    )
    chosen = decision.action
    assert chosen == "attack:wolf_1" or chosen.startswith(
        "move_"
    ), f"chose {chosen} at health 5 with a wolf biting"


async def test_night_does_not_mean_sleep_when_rested(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """Night, fatigue 30, a tree in reach: darkness alone is no reason to stop,
    and `sleep:ground` is offered at any fatigue above zero."""
    world = scene(
        "ada",
        make_entity("ada", HOME, fatigue=30, wielded="axe"),
        objects=[make_object("tree_1", "tree", (101, 100))],
        tick=NIGHT_TICK,
        clock=make_clock(NIGHT_TICK),
    )
    brief = Brief(
        instruction="Chop tree_1 for wood.",
        success_condition="you are carrying 6 wood",
        max_ticks=40,
    )
    decision, _ = await ask(
        jev, recorder, request, world, brief, expected="extract:tree_1"
    )
    assert "extract:tree_1" in top_keys(decision), f"top two {top_keys(decision)}"


# --- The social channel -----------------------------------------------------


async def test_walks_to_a_shout_for_help_when_the_brief_allows_it(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """A shout for help twenty tiles off, a brief that says go: the walk option
    exists and taking it is what makes twelve settlers a settlement."""
    shout_from = (HOME[0], HOME[1] + 20)
    world = scene(
        "ada",
        make_entity("ada", HOME, health=20, wielded="sword"),
        objects=[make_object("tree_1", "tree", (101, 100))],
        events=[utterance_event("dov", "Wolf! Help!", shout_from, channel="shout")],
    )
    brief = Brief(
        instruction="Chop wood near the site, but help anyone who shouts for help.",
        success_condition="you are carrying 6 wood",
        max_ticks=60,
    )
    key = f"{HEARD_SHOUT_KEY_PREFIX}dov"
    decision, criteria = await ask(jev, recorder, request, world, brief, expected=key)
    assert key in criteria, f"no walk to dov's shout in {sorted(criteria)}"
    assert key in top_keys(decision), f"top two {top_keys(decision)}"


async def test_ignores_the_shout_when_the_brief_forbids_it(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """The same shout, but the brief says finish the wall whatever happens and
    the actor has 3 health: running at a wolf fight is suicide, not help."""
    shout_from = (HOME[0], HOME[1] + 20)
    world = scene(
        "ada",
        make_entity("ada", HOME, health=3, inventory={"wood_wall": 2}),
        events=[utterance_event("dov", "Wolf! Help!", shout_from, channel="shout")],
    )
    brief = Brief(
        instruction=(
            "Finish the wall no matter what. Do not leave this spot for any "
            "reason; you are too hurt to fight."
        ),
        success_condition="you are carrying no wood_wall",
        max_ticks=40,
    )
    key = f"{HEARD_SHOUT_KEY_PREFIX}dov"
    decision, criteria = await ask(
        jev,
        recorder,
        request,
        world,
        brief,
        expected="place:wood_wall:* ; never the shout as first choice",
        extra={"forbidden": key},
    )
    assert key in criteria, "the scenario needs the temptation to exist"
    assert decision.action != key, f"chose {decision.action} at health 3"


async def test_hails_the_settler_the_brief_named(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """jory is standing next to you and the brief grants a hail to jory:
    `hail:jory` is the one option that starts the conversation."""
    world = scene(
        "ada",
        make_entity("ada", HOME),
        objects=[make_object("tree_1", "tree", (103, 103))],
        entities=[make_entity("jory", (101, 100))],
    )
    brief = Brief(
        instruction="Talk to jory about the plan for the wall.",
        success_condition="you have talked with jory",
        max_ticks=30,
        hails=(BriefHail("jory", "Jory, shall we settle the plan for the wall?"),),
    )
    decision, criteria = await ask(
        jev, recorder, request, world, brief, expected="hail:jory"
    )
    assert "hail:jory" in criteria, f"no hail offered in {sorted(criteria)}"
    assert decision.action == "hail:jory", f"chose {decision.action}"


async def test_shouts_the_warning_the_brief_gave_it(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """The brief hands over one phrase and says shout it when a wolf appears; a
    wolf has just appeared eight tiles away."""
    world = scene(
        "ada",
        make_entity("ada", HOME, wielded="axe"),
        objects=[make_object("tree_1", "tree", (101, 100))],
        entities=[make_entity("wolf_1", (100, 108), entity_type="wolf", health=16)],
    )
    brief = Brief(
        instruction="Chop wood, and shout the warning as soon as you see a wolf.",
        success_condition="you are carrying 6 wood",
        max_ticks=40,
        shouts=("Wolf near the river!",),
    )
    decision, criteria = await ask(
        jev, recorder, request, world, brief, expected="shout:0"
    )
    assert "shout:0" in criteria, f"no shout offered in {sorted(criteria)}"
    assert "shout:0" in top_keys(decision), f"top two {top_keys(decision)}"


# --- Spatial ----------------------------------------------------------------


async def test_steps_toward_a_tree_behind_a_wall_with_a_door(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """A wall line stands between the actor and the tree, with one door in it.
    There is a way through, so this is ordinary walking, not lost."""
    wall = [
        make_object(f"wood_wall_{index}", "wood_wall", (102, y))
        for index, y in enumerate(range(96, 105))
        if y != 100
    ]
    world = scene(
        "ada",
        make_entity("ada", HOME, wielded="axe"),
        objects=wall
        + [
            make_object("door_1", "door", (102, 100)),
            make_object("tree_1", "tree", (105, 100)),
        ],
    )
    brief = Brief(
        instruction="Go through the door and chop tree_1 for wood.",
        success_condition="you are carrying 4 wood",
        max_ticks=40,
    )
    decision, criteria = await ask(
        jev,
        recorder,
        request,
        world,
        brief,
        expected=f"{step_to('tree_1')} or move_E, lost below 0.45",
    )
    assert step_to("tree_1") in criteria, f"no walk to the tree in {sorted(criteria)}"
    assert (
        step_to("tree_1") in top_keys(decision) or decision.action == "move_E"
    ), f"top two {top_keys(decision)}"
    assert decision.lost < LOST_LOW, f"lost was {decision.lost}"


async def test_steps_toward_the_named_river_not_the_pond_it_can_see(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """Water three tiles east is not the river the brief named twenty-five tiles
    west; a named place is a coordinate, not the nearest thing that looks like it."""
    tiles = list(make_tiles(HOME)) + list(
        make_tiles((104, 100), 1, floor="shallow_water")
    )
    world = scene(
        "ada",
        make_entity("ada", HOME),
        tiles=tiles,
        objects=[make_object("tree_1", "tree", (103, 97))],
    )
    brief = Brief(
        instruction="Walk to the river and gather fiber from the reeds there.",
        success_condition="you are carrying 3 fiber",
        max_ticks=80,
        places={"river": (HOME[0] - 25, HOME[1])},
    )
    decision, criteria = await ask(
        jev, recorder, request, world, brief, expected=step_to("river")
    )
    assert step_to("river") in criteria, f"no walk to the river in {sorted(criteria)}"
    assert decision.action == step_to("river"), f"chose {decision.action}"


# --- Containers and journeys ------------------------------------------------


async def test_takes_the_axe_out_of_the_chest(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """An axe is in the chest beside you and your hands are empty: withdraw is
    the whole brief, and `deposit:` of your own wood is the near-miss beside it."""
    world = scene(
        "ada",
        make_entity("ada", HOME, wielded="", inventory={"wood": 2}),
        objects=[
            make_object(
                "chest_1", "chest", (101, 100), {"contents": '{"axe": 1, "stone": 3}'}
            ),
            make_object("tree_1", "tree", (99, 100)),
        ],
    )
    brief = Brief(
        instruction="Take an axe out of chest_1.",
        success_condition="you are carrying an axe",
        max_ticks=20,
    )
    decision, criteria = await ask(
        jev, recorder, request, world, brief, expected="withdraw:chest_1:axe"
    )
    assert "withdraw:chest_1:axe" in criteria, f"no withdraw in {sorted(criteria)}"
    assert decision.action == "withdraw:chest_1:axe", f"chose {decision.action}"


async def test_keeps_going_rather_than_abandoning_the_journey(
    jev: TypeSafeJevClient, recorder: ResultsRecorder, request: pytest.FixtureRequest
) -> None:
    """Mid-walk with seven steps left and nothing wrong: `stop_going` is always
    on the list, and picking it would throw the journey away for nothing."""
    world = scene(
        "ada",
        make_entity("ada", HOME, wielded="pickaxe"),
        objects=[make_object("rock_1", "rock_medium", (108, 100))],
    )
    travel = TravelState(
        target=(108, 100), label="rock_1 (rock_medium)", stop_adjacent=True
    )
    brief = Brief(
        instruction="Walk to rock_1 and mine it for stone.",
        success_condition="you are carrying 3 stone",
        max_ticks=40,
        travel=travel,
    )
    progress = StintProgress(
        ticks_used=1,
        ticks_left=39,
        actions={"step_towards": 1},
        moved_from_start=(1, 0),
        net_tiles_moved=1,
    )
    decision, criteria = await ask(
        jev,
        recorder,
        request,
        world,
        brief,
        expected=f"{KEEP_GOING} or {step_to('rock_1')}",
        progress=progress,
        extra={"forbidden": STOP_GOING},
    )
    assert STOP_GOING in criteria, "the scenario needs the temptation to exist"
    assert decision.action != STOP_GOING, f"chose {decision.action}"
    assert {KEEP_GOING, step_to("rock_1"), "move_E"} & set(
        top_keys(decision)
    ), f"top two {top_keys(decision)}"
