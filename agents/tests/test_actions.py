"""One rule per action, and the world's detail strings read in one place.

`actions.py` holds the preconditions and the intents both layers need, so the
planner and Jev can no longer disagree about whether an action is legal. The
exposure table records which layer may take which kind of action; the tests
here check it against the real planner tool names and the real option keys,
because an asymmetry that is only a comment is an asymmetry a refactor will
tidy away.

`outcomes.py` holds the reading of the world's own answer, and every parser in
it is tested against the exact wording `world/src/world/` writes.
"""

from __future__ import annotations

from typing import Mapping

import pytest

from agents.jev_agent import items
from agents.jev_agent.actions import (
    EXPOSURE,
    collect_here_attempt,
    deposit_attempt,
    dismantle_attempt,
    drop_attempt,
    eat_attempt,
    extract_attempt,
    give_attempt,
    hail_refusal,
    jev_action_kinds,
    pickup_attempt,
    place_attempt,
    planner_action_kinds,
    withdraw_attempt,
    write_sign_attempt,
)
from agents.jev_agent.geometry import NAME_TO_DIRECTION, NO_DIRECTION
from agents.jev_agent.options import enumerate_options
from agents.jev_agent.outcomes import (
    NO_DETAIL,
    ActionOutcome,
    CraftProgress,
    Given,
    heard_ids,
    parse_craft_progress,
    parse_gave,
    parse_seat,
    placed_object_id,
)
from agents.jev_agent.planner import PlannerDeps, build_planner_agent
from agents.jev_agent.worldmodel import WorldModel

from pydantic_ai.models.test import TestModel

from helpers import make_entity, make_object, make_observation
from test_planner import bridge, deps, world_model  # noqa: F401 - fixtures

EAST = NAME_TO_DIRECTION["E"]

# Planner tools whose name is not the action kind. `talk_to` is the hail, and
# the word "hail" never reaches the model.
TOOL_NAME_BY_KIND: Mapping[str, str] = {"hail": "talk_to"}

# Planner tools that take no world action of their own: they read, remember or
# register something, so the exposure table has nothing to say about them.
NON_ACTION_TOOLS: frozenset[str] = frozenset(
    {
        "look",
        "start_stint",
        "craft",
        "read_board",
        "remember",
        "recall",
        "set_reflex",
        "clear_reflex",
    }
)

# How an option key's head maps to an action kind. Walking is one kind however
# it is offered, and the two travel controls are walking too.
OPTION_HEAD_TO_KIND: Mapping[str, str] = {
    "move": "move",
    "step_towards": "move",
    "keep_going": "move",
    "stop_going": "move",
    "wait": "wait",
    "eat": "eat",
    "collect": "collect",
    "extract": "extract",
    "attack": "attack",
    "craft": "craft",
    "equip": "equip",
    "place": "place",
    "rest": "rest",
    "sleep": "sleep",
    "wake": "wake",
    "pickup": "pickup",
    "deposit": "deposit",
    "withdraw": "withdraw",
    "shout": "shout",
    "hail": "hail",
    "join_conversation": "join_conversation",
}


def _model(**kwargs: object) -> WorldModel:
    """A world model for `ada` at (10, 10) after one observation."""
    model = WorldModel("ada")
    self_entity = kwargs.pop("self_entity", make_entity("ada", (10, 10)))
    model.update(make_observation(1, self_entity, **kwargs))  # type: ignore[arg-type]
    return model


def _option_head(key: str) -> str:
    """The action kind an option key belongs to."""
    head = key.split(":")[0]
    if head.startswith("move_"):
        return "move"
    return head


# --- the exposure table -----------------------------------------------------


async def _tool_names(deps: PlannerDeps) -> set[str]:
    """The names the model is actually offered, from a run that calls none."""
    agent = build_planner_agent("test")
    model = TestModel(call_tools=[])
    with agent.override(model=model):
        await agent.run("go", deps=deps)
    return {tool.name for tool in model.last_model_request_parameters.function_tools}


async def test_every_planner_action_kind_has_a_tool_of_that_name(
    deps: PlannerDeps,
) -> None:
    tool_names = await _tool_names(deps)
    for kind in sorted(planner_action_kinds()):
        wanted = TOOL_NAME_BY_KIND.get(kind, kind)
        assert wanted in tool_names, f"{kind} claims a planner tool called {wanted}"


async def test_no_planner_tool_exists_for_a_kind_the_planner_is_denied(
    deps: PlannerDeps,
) -> None:
    """`move`, `attack`, `extract` and `collect` go through Jev, by decision."""
    tool_names = await _tool_names(deps)
    denied = {kind for kind, e in EXPOSURE.items() if not e.planner}
    assert denied & tool_names == set()
    assert {"move", "attack", "extract", "collect"} <= denied


async def test_every_planner_tool_is_an_action_kind_or_declared_not_one(
    deps: PlannerDeps,
) -> None:
    tool_names = await _tool_names(deps)
    named = {TOOL_NAME_BY_KIND.get(kind, kind) for kind in planner_action_kinds()}
    assert tool_names - named - NON_ACTION_TOOLS == set()


def test_no_option_key_belongs_to_a_kind_jev_is_denied() -> None:
    """A rich tick, so most of the option layer actually fires."""
    model = _model(
        self_entity=make_entity(
            "ada",
            (10, 10),
            inventory={"berry": 2, "wood_wall": 1, "axe": 1, "road": 1},
            fatigue=60,
        ),
        objects=[
            make_object("tree_1", "tree", (11, 10)),
            make_object("chest_1", "chest", (9, 10), {"contents": '{"berry": 3}'}),
            make_object("sign_1", "sign", (12, 12), {"text": "east"}),
        ],
    )
    heads = {_option_head(option.key) for option in enumerate_options(model)}
    allowed = jev_action_kinds()
    for head in sorted(heads):
        kind = OPTION_HEAD_TO_KIND[head]
        assert kind in allowed, f"{head} is offered to Jev but the table denies it"


def test_the_deliberate_asymmetries_are_written_down_with_a_reason() -> None:
    for kind in ("dismantle", "place_sign", "build", "write_sign", "write_note"):
        exposure = EXPOSURE[kind]
        assert not exposure.jev
        assert exposure.planner
        assert exposure.note


# --- preconditions both layers now share ------------------------------------


def test_eat_refuses_an_empty_pack_without_spending_a_tick() -> None:
    attempt = eat_attempt(_model(), items.BERRY)

    assert not attempt.allowed
    assert "you carry no berry" in attempt.refusal
    assert "No tick spent." in attempt.refusal


def test_collect_needs_a_bush_with_a_berry_underfoot() -> None:
    empty = _model(objects=[make_object("bush_1", "bush", (11, 10))])
    assert not collect_here_attempt(empty).allowed

    underfoot = _model(
        objects=[make_object("bush_1", "bush", (10, 10), {"berry_count": "1"})]
    )
    attempt = collect_here_attempt(underfoot)
    assert attempt.allowed
    assert attempt.intent.collect.object_id == "bush_1"


def test_extract_refuses_a_vein_the_wielded_tool_cannot_bite() -> None:
    model = _model(objects=[make_object("iron_1", items.IRON_VEIN, (11, 10))])

    attempt = extract_attempt(model, "iron_1")

    assert not attempt.allowed
    assert "you hold nothing" in attempt.refusal


def test_extract_refuses_something_out_of_reach() -> None:
    model = _model(objects=[make_object("tree_9", "tree", (15, 10))])

    assert "5 tiles away" in extract_attempt(model, "tree_9").refusal


def test_dismantle_refuses_anything_nobody_placed() -> None:
    model = _model(objects=[make_object("tree_1", "tree", (11, 10))])

    assert "nobody placed" in dismantle_attempt(model, "tree_1").refusal


def test_pickup_needs_the_pile_under_your_own_feet() -> None:
    beside = _model(
        objects=[
            make_object("pile_1", items.ITEM_PILE, (11, 10), {"contents": '{"axe": 1}'})
        ]
    )
    assert "no item pile lies on the tile you stand on" in (
        pickup_attempt(beside, "axe").refusal
    )

    under = _model(
        objects=[
            make_object("pile_1", items.ITEM_PILE, (10, 10), {"contents": '{"axe": 1}'})
        ]
    )
    assert pickup_attempt(under, "axe").allowed
    assert "holds no rope" in pickup_attempt(under, "rope").refusal


def test_drop_and_give_need_the_item_in_the_pack() -> None:
    model = _model(self_entity=make_entity("ada", (10, 10), inventory={"wood": 2}))

    assert drop_attempt(model, "wood").allowed
    assert "you carry no plank to drop" in drop_attempt(model, "plank").refusal
    assert give_attempt(model, "mira", "wood", 1).allowed
    assert "you carry no plank to give" in give_attempt(model, "mira", "plank").refusal


def test_chest_moves_need_a_chest_within_reach_and_the_goods() -> None:
    model = _model(
        self_entity=make_entity("ada", (10, 10), inventory={"wood": 2}),
        objects=[make_object("chest_1", "chest", (11, 10), {"contents": '{"axe": 1}'})],
    )

    assert deposit_attempt(model, "chest_1", "wood", 2).allowed
    assert withdraw_attempt(model, "chest_1", "axe").allowed
    assert "holds no rope" in withdraw_attempt(model, "chest_1", "rope").refusal
    assert "never seen a chest" in deposit_attempt(model, "chest_9", "wood").refusal


def test_place_refuses_an_occupied_tile_and_names_the_free_sides() -> None:
    model = _model(
        self_entity=make_entity("ada", (10, 10), inventory={"door": 1}),
        objects=[make_object("wood_wall_16", "wood_wall", (11, 10))],
    )

    refusal = place_attempt(model, items.DOOR, EAST).refusal

    assert "(11, 10) holds wood_wall_16" in refusal
    assert "neighbouring tiles that would take a door:" in refusal


def test_place_refuses_a_ground_piece_given_a_direction() -> None:
    model = _model(self_entity=make_entity("ada", (10, 10), inventory={"road": 1}))

    assert "goes on the tile you stand on" in (
        place_attempt(model, items.ROAD, EAST).refusal
    )
    assert place_attempt(model, items.ROAD, NO_DIRECTION).allowed


def test_a_hail_is_refused_for_a_settler_that_is_asleep_or_unseen() -> None:
    model = _model(entities=[make_entity("mira", (11, 10), asleep=True)])

    assert "asleep" in hail_refusal(model, "mira")
    assert "never seen" in hail_refusal(model, "dov")
    assert "yourself" in hail_refusal(model, "ada")


def test_write_sign_refuses_a_message_board_and_names_the_right_tool() -> None:
    model = _model(objects=[make_object("board_1", "message_board", (11, 10))])

    assert "use write_note" in write_sign_attempt(model, "board_1", "hi").refusal


# --- the world's detail strings ---------------------------------------------


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        ("placed sign_12 at (3, 4)", "sign_12"),
        ("placed wood_wall_2 at (0, 0)", "wood_wall_2"),
        ("crafted plank", ""),
        ("", ""),
    ],
)
def test_the_placed_detail_is_read_in_one_place(detail: str, expected: str) -> None:
    assert placed_object_id(detail) == expected


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        ("heard: ada, bram", ("ada", "bram")),
        ("heard: ", ()),
        ("heard:", ()),
        ("thought", ()),
    ],
)
def test_the_heard_detail_is_read_in_one_place(
    detail: str, expected: tuple[str, ...]
) -> None:
    assert heard_ids(detail) == expected


def test_the_gave_detail_is_read_in_one_place() -> None:
    assert parse_gave("gave 3 stone to mira") == Given(3, "stone", "mira")
    assert not parse_gave("dropped 3 stone").happened


def test_the_craft_progress_state_is_read_in_one_place() -> None:
    assert parse_craft_progress("bed:2") == CraftProgress("bed", 2)
    assert parse_craft_progress("bed:x") == CraftProgress()
    assert parse_craft_progress("") == CraftProgress()


def test_a_converse_detail_is_split_in_one_place() -> None:
    seat = parse_seat("hail conv_12 mira")
    assert (seat.action, seat.conversation_id, seat.target) == (
        "hail",
        "conv_12",
        "mira",
    )
    assert parse_seat("open conv_1").target == ""
    assert parse_seat("nope").conversation_id == ""


# --- the one rendering ------------------------------------------------------


def test_an_outcome_renders_exactly_what_the_planner_used_to_be_shown() -> None:
    ok = ActionOutcome.from_event(
        "place door E", "place", True, "placed door_1 at (1, 2)"
    )
    assert ok.text() == "place door E -> place ok: placed door_1 at (1, 2)"
    assert ok.placed_object_id == "door_1"

    failed = ActionOutcome.from_event("place door E", "place", False, "")
    assert failed.text() == f"place door E -> place failed: {NO_DETAIL}"

    nothing = ActionOutcome.submitted_only("wait 2 ticks")
    assert nothing.text() == "wait 2 ticks -> submitted"

    cut = ActionOutcome.never_ran("eat berry", "interrupted: reflex stint started")
    assert cut.text() == "eat berry -> interrupted: reflex stint started"
    assert cut.interrupted
    assert not cut.ok
