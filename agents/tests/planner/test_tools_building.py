"""The building tools: build, craft, place, dismantle and rest."""

from __future__ import annotations

import json
from pathlib import Path
import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from agents import world_pb2 as pb
from agents.jev_agent import items, planner as planner_module
from agents.jev_agent.planner.tools import building as planner_building
from agents.jev_agent.build import BuildExecutor
from agents.jev_agent.items import RECIPES
from agents.jev_agent.planner import PlannerDeps, build_planner_agent, parse_tile_list
from agents.jev_agent.outcomes import ActionOutcome
from agents.jev_agent.worldmodel import WorldModel
from helpers import make_entity, make_object, make_observation

from conftest import (
    RecordingBridge,
    _call_tool,
    _returned_text,
    bridge,
    carrying,
    deps,
    one_tool_call,
    world_model,
)


def test_a_place_name_must_be_short_lowercase_and_unused(
    world_model: WorldModel,
) -> None:
    assert planner_module.validated_places({"the_lake": [3, 4]}, world_model) == {
        "the_lake": (3, 4)
    }
    with pytest.raises(ModelRetry, match="at most"):
        planner_module.validated_places(
            {f"p{i}": [0, 0] for i in range(7)}, world_model
        )
    with pytest.raises(ModelRetry, match="lowercase"):
        planner_module.validated_places({"The Lake": [3, 4]}, world_model)
    with pytest.raises(ModelRetry, match="already the id"):
        planner_module.validated_places({"tree_1": [3, 4]}, world_model)
    with pytest.raises(ModelRetry, match="already the id"):
        planner_module.validated_places({"bob": [3, 4]}, world_model)
    with pytest.raises(ModelRetry, match=r"\[x, y\] pair"):
        planner_module.validated_places({"the_lake": [3]}, world_model)


async def test_travel_to_builds_a_brief_with_a_preset_travel(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    with agent.override(model=TestModel(call_tools=["travel_to"])):
        await agent.run("go", deps=deps)
    brief = bridge.briefs[0]
    assert brief.travel is not None
    assert brief.instruction == "Walk to the destination."
    # Jev is given the offset to a named place, never the numbers themselves.
    assert list(brief.places) == ["destination"]


async def test_craft_reports_an_unknown_recipe_instead_of_submitting_it(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    with agent.override(model=TestModel(call_tools=["craft"])):
        result = await agent.run("go", deps=deps)
    if bridge.actions:
        assert all(intent.HasField("craft") for intent, _ in bridge.actions)
    else:
        assert "no such recipe" in result.output


def test_tile_lists_parse_and_complain_clearly() -> None:
    assert parse_tile_list("12,30; (13,31)") == [(12, 30), (13, 31)]
    assert parse_tile_list("") == []
    with pytest.raises(ModelRetry):
        parse_tile_list("12")
    with pytest.raises(ModelRetry):
        parse_tile_list("x,y")


async def test_build_hands_the_bridge_a_driver_with_the_planned_tiles(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    carrying(bridge.model, {"wood_wall": 3})
    agent = build_planner_agent("test")
    model = one_tool_call(
        "build",
        {
            "kind": "wood_wall",
            "shape": "rect",
            "x1": 10,
            "y1": 10,
            "x2": 13,
            "y2": 13,
            "max_ticks": 40,
            "skip": "11,10",
        },
    )
    with agent.override(model=model):
        await agent.run("go", deps=deps)

    driver = bridge.drivers[0]
    assert isinstance(driver, BuildExecutor)
    assert (11, 10) not in driver.plan.tiles
    assert len(driver.plan.tiles) == 11
    assert bridge.briefs[0].max_ticks == 40
    assert "wood_wall" in bridge.briefs[0].instruction


async def test_build_with_no_pieces_runs_nothing_and_names_the_recipe(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    model = one_tool_call(
        "build",
        {"kind": "wood_wall", "shape": "line", "x1": 10, "y1": 12, "x2": 13, "y2": 12},
    )
    with agent.override(model=model):
        result = await agent.run("go", deps=deps)
    returned = [
        part.content
        for message in result.all_messages()
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert not bridge.briefs, "no stint is started for an empty pack"
    text = str(returned[0])
    assert "you carry no wood_wall" in text
    assert "needs 4" in text
    assert "craft wood_wall 4 times" in text
    assert "8 plank in all" in text


async def test_build_asks_again_when_the_shape_makes_no_sense(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    model = one_tool_call(
        "build",
        {"kind": "berry", "shape": "line", "x1": 0, "y1": 0, "x2": 1, "y2": 0},
    )
    with agent.override(model=model):
        await agent.run("go", deps=deps)
    assert not bridge.drivers


async def test_a_ground_piece_can_be_placed_without_a_direction(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    bridge.model.update(
        make_observation(6, make_entity("ada", (10, 10), inventory={"road": 1}))
    )
    agent = build_planner_agent("test")
    with agent.override(model=one_tool_call("place", {"kind": "road"})):
        await agent.run("go", deps=deps)
    intent, _ = bridge.actions[-1]
    assert intent.place.kind == "road"
    assert intent.place.direction == pb.DIRECTION_UNSPECIFIED


async def test_dismantle_and_rest_submit_their_intents(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    bridge.model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10)),
            objects=[make_object("wood_wall_1", "wood_wall", (11, 10))],
        )
    )
    agent = build_planner_agent("test")
    with agent.override(model=one_tool_call("rest", {"object_id": "bed_1"})):
        await agent.run("go", deps=deps)
    with agent.override(model=one_tool_call("dismantle", {"object_id": "wood_wall_1"})):
        await agent.run("go", deps=deps)
    submitted = [intent for intent, _ in bridge.actions]
    assert any(intent.HasField("rest") for intent in submitted)
    assert any(intent.HasField("extract") for intent in submitted)


async def test_dismantle_refuses_a_piece_out_of_reach(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    """The planner used to spend a tick finding out; now it is told the fact."""
    bridge.model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10)),
            objects=[make_object("wood_wall_9", "wood_wall", (15, 10))],
        )
    )
    agent = build_planner_agent("test")
    with agent.override(model=one_tool_call("dismantle", {"object_id": "wood_wall_9"})):
        result = await agent.run("go", deps=deps)
    assert "5 tiles away" in _returned_text(result)[0]
    assert bridge.actions == []


async def test_craft_repeats_the_action_until_the_recipe_completes(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    bridge.direct_results = [
        "craft charcoal -> craft ok: charcoal 1/2",
        "craft charcoal -> craft ok: crafted charcoal x2",
    ]
    agent = build_planner_agent("test")
    with agent.override(model=_call_tool("craft", {"recipe": "charcoal"})):
        result = await agent.run("go", deps=deps)

    assert len(bridge.actions) == 2
    assert all(intent.craft.recipe == "charcoal" for intent, _ in bridge.actions)
    assert "charcoal 1/2" in result.output
    assert "crafted charcoal" in result.output


async def test_craft_stops_at_the_first_failed_action(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    bridge.direct_results = [
        "craft iron_ingot -> craft failed: iron_ingot needs a furnace nearby"
    ]
    agent = build_planner_agent("test")
    with agent.override(model=_call_tool("craft", {"recipe": "iron_ingot"})):
        result = await agent.run("go", deps=deps)

    assert len(bridge.actions) == 1
    assert "needs a furnace nearby" in result.output


async def test_a_hand_recipe_still_takes_exactly_one_craft_action(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    bridge.direct_result = "craft plank -> craft ok: crafted plank x2"
    agent = build_planner_agent("test")
    with agent.override(model=_call_tool("craft", {"recipe": "plank"})):
        await agent.run("go", deps=deps)
    assert len(bridge.actions) == 1


async def test_eat_with_nothing_to_eat_names_the_nearest_berry_bushes(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    bridge.model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10)),
            objects=[
                make_object("bush_7", "bush", (14, 10), {"berry_count": "1"}),
                make_object("bush_8", "bush", (12, 10), {"berry_count": "0"}),
            ],
        )
    )
    agent = build_planner_agent("test")
    with agent.override(model=one_tool_call("eat", {})):
        result = await agent.run("go", deps=deps)
    assert not bridge.actions, "nothing is submitted, so no tick is spent"
    text = _returned_text(result)[0]
    assert "you carry no berry, and there is none to pick where you stand" in text
    assert "bush bush_7 at (14, 10) (d4): berry (in view)" in text
    assert "bush_8" not in text


class CraftingBridge(RecordingBridge):
    """A bridge whose `craft` actions really change the pack, as the world does."""

    def __init__(self, world_model: WorldModel, inventory: dict[str, int]) -> None:
        super().__init__(world_model)
        self.inventory = dict(inventory)
        self._observe()

    def _observe(self) -> None:
        self.model.update(
            make_observation(
                self.model.tick + 1,
                make_entity("ada", (10, 10), inventory=self.inventory),
                objects=self.objects,
            )
        )

    objects: list[pb.WorldObject] = []

    async def direct_action(self, intent: pb.Intent, description: str) -> ActionOutcome:
        self.actions.append((intent, description))
        if not intent.HasField("craft"):
            return ActionOutcome.from_event(description, "action", True, "")
        recipe = RECIPES[intent.craft.recipe]
        for kind, amount in recipe.inputs:
            if self.inventory.get(kind, 0) < amount:
                self._observe()
                return ActionOutcome.from_event(
                    description, "craft", False, f"not enough {kind}"
                )
            self.inventory[kind] -= amount
        self.inventory[intent.craft.recipe] = (
            self.inventory.get(intent.craft.recipe, 0) + recipe.output_count
        )
        self._observe()
        return ActionOutcome.from_event(
            description, "craft", True, f"crafted {intent.craft.recipe}"
        )


def _build_call(kind: str) -> FunctionModel:
    return one_tool_call(
        "build",
        {
            "kind": kind,
            "shape": "line",
            "x1": 10,
            "y1": 12,
            "x2": 12,
            "y2": 12,
            "max_ticks": 60,
        },
    )


async def test_build_crafts_the_pieces_and_their_planks_before_it_starts(
    world_model: WorldModel, tmp_path: Path
) -> None:
    bridge = CraftingBridge(world_model, {"wood": 6})
    deps = PlannerDeps(bridge=bridge, memory_path=tmp_path / "memory.md")
    agent = build_planner_agent("test")
    with agent.override(model=_build_call("wood_wall")):
        result = await agent.run("go", deps=deps)
    assert bridge.inventory["wood_wall"] == 3
    assert bridge.briefs, "the build ran once it had pieces"
    assert "for this build, crafted 6 plank, 3 wood_wall" in _returned_text(result)[0]


async def test_build_says_what_it_could_not_craft_and_starts_nothing(
    world_model: WorldModel, tmp_path: Path
) -> None:
    bridge = CraftingBridge(world_model, {"stone": 4})
    deps = PlannerDeps(bridge=bridge, memory_path=tmp_path / "memory.md")
    agent = build_planner_agent("test")
    with agent.override(model=_build_call("wood_wall")):
        result = await agent.run("go", deps=deps)
    assert not bridge.briefs
    text = _returned_text(result)[0]
    assert "you carry no wood_wall" in text
    assert "nothing crafted: plank takes 1 wood and you carry 0" in text


async def test_build_will_not_craft_a_station_recipe_away_from_its_station(
    world_model: WorldModel, tmp_path: Path
) -> None:
    bridge = CraftingBridge(world_model, {"stone": 9, "clay": 9})
    deps = PlannerDeps(bridge=bridge, memory_path=tmp_path / "memory.md")
    agent = build_planner_agent("test")
    with agent.override(model=_build_call("stone_wall")):
        result = await agent.run("go", deps=deps)
    assert not bridge.briefs
    assert "stone_wall is made at a workshop_table, and there is none on or " in (
        _returned_text(result)[0]
    )


async def test_build_crafts_a_station_recipe_beside_its_station(
    world_model: WorldModel, tmp_path: Path
) -> None:
    bridge = CraftingBridge(world_model, {"stone": 9, "clay": 9})
    bridge.objects = [make_object("table_1", "workshop_table", (11, 10))]
    bridge._observe()
    deps = PlannerDeps(bridge=bridge, memory_path=tmp_path / "memory.md")
    agent = build_planner_agent("test")
    with agent.override(model=_build_call("stone_wall")):
        await agent.run("go", deps=deps)
    assert bridge.inventory["stone_wall"] == 3
    assert bridge.briefs


async def test_a_build_that_runs_dry_crafts_again_and_gives_up_in_the_end(
    world_model: WorldModel, tmp_path: Path
) -> None:
    bridge = CraftingBridge(world_model, {"wood": 40})
    # Every stint reports the same empty pack, so the tool refills and retries
    # until its resupply rounds run out rather than looping forever.
    bridge.stint_end_reason = "build_out_of_items"
    deps = PlannerDeps(bridge=bridge, memory_path=tmp_path / "memory.md")
    agent = build_planner_agent("test")
    with agent.override(model=_build_call("wood_wall")):
        await agent.run("go", deps=deps)
    assert len(bridge.briefs) == planner_building.BUILD_RESUPPLY_ROUNDS


async def test_a_failed_craft_says_where_the_missing_input_comes_from(
    deps: PlannerDeps, bridge: RecordingBridge, world_model: WorldModel
) -> None:
    """`craft bed -> bed needs 4 plank + 3 fiber` never mentioned reeds."""
    world_model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10), inventory={"plank": 4}),
            objects=[make_object("reeds_9", "reeds", (14, 12))],
        )
    )
    bridge.direct_result = "craft bed -> craft failed: bed needs 4 plank + 3 fiber"
    agent = build_planner_agent("test")
    with agent.override(model=_call_tool("craft", {"recipe": "bed"})):
        result = await agent.run("go", deps=deps)
    assert "fiber comes from reeds (bare hands)" in result.output
    assert "reeds_9 at (14, 12)" in result.output


async def test_a_failed_craft_names_no_source_for_a_crafted_input(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    bridge.direct_result = "craft wood_wall -> craft failed: needs 2 plank"
    agent = build_planner_agent("test")
    with agent.override(model=_call_tool("craft", {"recipe": "wood_wall"})):
        result = await agent.run("go", deps=deps)
    assert "comes from" not in result.output


async def test_place_crafts_the_piece_when_the_inputs_are_in_the_pack(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    bridge.model.update(
        make_observation(
            6, make_entity("ada", (10, 10), inventory={"wood": 6, "stone": 4})
        )
    )
    agent = build_planner_agent("test")
    with agent.override(
        model=one_tool_call("place", {"kind": "chest", "direction": "N"})
    ):
        await agent.run("go", deps=deps)
    kinds = [
        intent.craft.recipe for intent, _ in bridge.actions if intent.HasField("craft")
    ]
    assert "chest" in kinds


async def test_place_without_the_inputs_names_the_shortfall_and_places_nothing(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")
    with agent.override(
        model=one_tool_call("place", {"kind": "chest", "direction": "N"})
    ):
        result = await agent.run("go", deps=deps)
    text = _returned_text(result)[0]
    assert "chest takes" in text
    assert not [i for i, _ in bridge.actions if i.HasField("place")]


def test_a_failed_craft_says_where_the_missing_material_grows(
    world_model: WorldModel,
) -> None:
    lines = planner_module.missing_input_lines(world_model, RECIPES[items.BED])
    text = "\n".join(lines)
    assert "fiber comes from reeds (bare hands); reeds grow on the banks" in text
    assert "you know of none yet" in text


async def test_a_refused_place_names_what_is_on_the_tile_and_the_free_sides(
    deps: PlannerDeps, bridge: RecordingBridge
) -> None:
    """Three blind `place door` calls all failed "target already holds an object"."""
    bridge.model.update(
        make_observation(
            6,
            make_entity("ada", (10, 10), inventory={"door": 1}),
            objects=[make_object("wood_wall_16", "wood_wall", (11, 10))],
        )
    )
    bridge.direct_result = (
        "place door E -> place failed: target already holds an object"
    )
    agent = build_planner_agent("test")
    with agent.override(model=_call_tool("place", {"kind": "door", "direction": "E"})):
        result = await agent.run("go", deps=deps)
    text = _returned_text(result)[0]
    assert "(11, 10) holds wood_wall_16" in text
    assert "neighbouring tiles that would take a door:" in text


def _build_call_with_doors(kind: str, door: str) -> FunctionModel:
    return one_tool_call(
        "build",
        {
            "kind": kind,
            "shape": "rect",
            "x1": 10,
            "y1": 12,
            "x2": 13,
            "y2": 15,
            "max_ticks": 120,
            "door": door,
        },
    )


async def test_build_places_a_door_on_the_tiles_it_is_given(
    world_model: WorldModel, tmp_path: Path
) -> None:
    """esme closed a doorless ring around the settlement's only workshop_table."""
    bridge = CraftingBridge(world_model, {"wood": 60, "fiber": 4})
    bridge.objects = [make_object("table_1", "workshop_table", (11, 10))]
    bridge._observe()
    deps = PlannerDeps(bridge=bridge, memory_path=tmp_path / "memory.md")
    agent = build_planner_agent("test")
    with agent.override(model=_build_call_with_doors("wood_wall", "10,13")):
        result = await agent.run("go", deps=deps)
    # 12 perimeter tiles, one of them a door, so 11 walls and 1 door.
    assert bridge.inventory["wood_wall"] == 11
    assert bridge.inventory["door"] == 1
    text = _returned_text(result)[0]
    assert "doors (1 tile(s)):" in text
    assert len(bridge.briefs) == 2


async def test_a_door_tile_off_the_shape_is_refused(
    world_model: WorldModel, tmp_path: Path
) -> None:
    bridge = CraftingBridge(world_model, {"wood": 60})
    deps = PlannerDeps(bridge=bridge, memory_path=tmp_path / "memory.md")
    seen: list[str] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        last_part = messages[-1].parts[-1]
        if isinstance(last_part, RetryPromptPart):
            seen.append(str(last_part.content))
            return ModelResponse(parts=[TextPart("done")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "build",
                    json.dumps(
                        {
                            "kind": "wood_wall",
                            "shape": "rect",
                            "x1": 10,
                            "y1": 12,
                            "x2": 13,
                            "y2": 15,
                            "door": "99,99",
                        }
                    ),
                )
            ]
        )

    agent = build_planner_agent("test")
    with agent.override(model=FunctionModel(respond)):
        await agent.run("go", deps=deps)
    assert seen and "door tiles must be tiles of the shape itself" in seen[0]
    assert not bridge.briefs


async def test_a_door_that_cannot_be_made_leaves_the_tile_a_gap(
    world_model: WorldModel, tmp_path: Path
) -> None:
    bridge = CraftingBridge(world_model, {"wood": 60})  # no fiber -> no rope -> no door
    bridge.objects = [make_object("table_1", "workshop_table", (11, 10))]
    bridge._observe()
    deps = PlannerDeps(bridge=bridge, memory_path=tmp_path / "memory.md")
    agent = build_planner_agent("test")
    with agent.override(model=_build_call_with_doors("wood_wall", "10,13")):
        result = await agent.run("go", deps=deps)
    text = _returned_text(result)[0]
    assert "you carry no door" in text
    assert "fiber comes from reeds" in text
