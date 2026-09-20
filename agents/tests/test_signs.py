"""Signs, agent side: the read guarantee, what Jev sees, and the planner tools.

Contract: docs/08_building.md, "Signs".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import pytest
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart

from agents.jev_agent import items
from agents.jev_agent.jevstate import build_state, render_map
from agents.jev_agent.options import JEV_PLACEABLE_KINDS, enumerate_options
from agents.jev_agent.planner import (
    PlannerDeps,
    build_planner_agent,
    describe_world,
)
from agents.jev_agent.stint import Brief, Stint
from agents.jev_agent.worldmodel import WorldModel

from helpers import FakeJevClient, make_entity, make_object, make_observation
from test_planner import RecordingBridge  # noqa: F401 - fixtures live there


def sign_object(
    object_id: str,
    position: tuple[int, int],
    text: str = "",
    author: str = "",
    tick: int = 0,
):
    """A proto sign object with the three state keys the world writes."""
    return make_object(
        object_id,
        items.SIGN,
        position,
        {"text": text, "author": author, "tick": str(tick) if tick else ""},
    )


def observe(
    model: WorldModel,
    tick: int,
    *objects,
    position: tuple[int, int] = (10, 10),
    inventory: Mapping[str, int] | None = None,
):
    """Fold one observation carrying `objects` into `model`."""
    return model.update(
        make_observation(
            tick,
            make_entity(model.entity_id, position, inventory=inventory),
            objects=objects,
        )
    )


# -- the read guarantee ------------------------------------------------------


class TestReadingSigns:
    def test_a_written_sign_in_view_is_delivered_once(self) -> None:
        model = WorldModel("ada")

        first = observe(
            model, 5, sign_object("sign_1", (12, 10), "wolves north", "bob", 4)
        )
        second = observe(
            model, 6, sign_object("sign_1", (12, 10), "wolves north", "bob", 4)
        )

        assert first.sign_notes == [
            '[sign at (12, 10) by bob, written tick 4: "wolves north"]'
        ]
        assert second.sign_notes == []

    def test_a_changed_text_is_delivered_again(self) -> None:
        model = WorldModel("ada")
        observe(model, 5, sign_object("sign_1", (12, 10), "wolves north", "bob", 4))

        digest = observe(
            model, 6, sign_object("sign_1", (12, 10), "wolves gone", "bob", 6)
        )

        assert digest.sign_notes == [
            '[sign at (12, 10) by bob, written tick 6: "wolves gone"]'
        ]

    def test_a_blank_sign_says_nothing(self) -> None:
        model = WorldModel("ada")

        digest = observe(model, 5, sign_object("sign_1", (12, 10)))

        assert digest.sign_notes == []

    def test_writing_on_a_blank_sign_seen_earlier_is_delivered(self) -> None:
        model = WorldModel("ada")
        observe(model, 5, sign_object("sign_1", (12, 10)))

        digest = observe(model, 6, sign_object("sign_1", (12, 10), "hello", "bob", 6))

        assert len(digest.sign_notes) == 1

    def test_your_own_sign_is_never_read_back_to_you(self) -> None:
        model = WorldModel("ada")

        digest = observe(model, 5, sign_object("sign_1", (12, 10), "mine", "ada", 4))

        assert digest.sign_notes == []

    def test_someone_else_rewriting_your_sign_is_delivered(self) -> None:
        model = WorldModel("ada")
        observe(model, 5, sign_object("sign_1", (12, 10), "mine", "ada", 4))

        digest = observe(model, 6, sign_object("sign_1", (12, 10), "theirs", "bob", 6))

        assert len(digest.sign_notes) == 1


async def test_a_sign_read_during_a_stint_is_in_the_stint_report(
    tmp_path: Path,
) -> None:
    model = WorldModel("ada")
    brief = Brief(instruction="chop", success_condition="you carry wood", max_ticks=5)
    stint = Stint(brief, model, FakeJevClient(), trace=_trace(tmp_path))

    digest = observe(
        model, 5, sign_object("sign_1", (12, 10), "wolves north", "bob", 4)
    )
    await stint.decide(digest)
    stint.finish("eject")

    text = stint.build_report().to_text()
    assert '[sign at (12, 10) by bob, written tick 4: "wolves north"]' in text


def _trace(tmp_path: Path):
    from agents.jev_agent.tracelog import AgentTrace

    return AgentTrace("ada", tmp_path)


# -- what Jev sees -----------------------------------------------------------


class TestJevSeesSigns:
    def _model(self) -> WorldModel:
        model = WorldModel("ada")
        observe(model, 5, sign_object("sign_1", (12, 10), "wolves north", "bob", 4))
        return model

    def test_the_map_draws_a_sign_as_S(self) -> None:
        picture = render_map(self._model())

        assert "S" in picture

    def test_nearby_carries_the_signs_text(self) -> None:
        state = build_state(
            self._model(), instruction="look", success_condition="never"
        )

        entry = next(e for e in state["nearby"] if e["type"] == items.SIGN)
        assert entry["text"] == "wolves north"
        assert entry["written_by"] == "bob"

    def test_jev_is_offered_a_step_toward_a_sign(self) -> None:
        options = enumerate_options(self._model(), None)

        assert any(option.key == "step_towards:sign_1" for option in options)

    def test_jev_is_never_offered_a_blank_sign_to_place(self) -> None:
        assert items.SIGN not in JEV_PLACEABLE_KINDS
        model = WorldModel("ada")
        model.update(
            make_observation(5, make_entity("ada", (10, 10), inventory={items.SIGN: 2}))
        )

        options = enumerate_options(model, None)

        assert not any(option.key.startswith("place:sign") for option in options)


# -- the planner -------------------------------------------------------------


def _call_tool(tool_name: str, args: Mapping[str, object]) -> FunctionModel:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart(tool_name, json.dumps(args))])
        return ModelResponse(parts=[TextPart(str(messages[-1].parts[0].content))])

    return FunctionModel(respond)


@pytest.fixture
def sign_model() -> WorldModel:
    model = WorldModel("ada")
    # A sign already in the pack: `place_sign`'s own craft-if-needed step is
    # covered separately below.
    observe(
        model,
        5,
        sign_object("sign_1", (12, 10), "wolves north", "bob", 4),
        inventory={"sign": 1},
    )
    return model


@pytest.fixture
def sign_bridge(sign_model: WorldModel) -> RecordingBridge:
    return RecordingBridge(sign_model)


@pytest.fixture
def sign_deps(sign_bridge: RecordingBridge, tmp_path: Path) -> PlannerDeps:
    return PlannerDeps(bridge=sign_bridge, memory_path=tmp_path / "memory.md")


def test_look_lists_signs_with_their_text(sign_model: WorldModel) -> None:
    text = describe_world(sign_model)

    assert "signs:" in text
    assert 'sign_1 at (12, 10) (in view): "wolves north" (by bob, tick 4)' in text


async def test_place_sign_places_then_writes(
    sign_deps: PlannerDeps, sign_bridge: RecordingBridge
) -> None:
    sign_bridge.direct_results = [
        "place sign E -> place ok: placed sign_9 at (11, 10)",
        'write "keep out" on sign_9 -> write_note ok: wrote sign_9: "keep out"',
    ]
    agent = build_planner_agent("test")

    with agent.override(
        model=_call_tool("place_sign", {"direction": "E", "text": "keep out"})
    ):
        result = await agent.run("go", deps=sign_deps)

    place_intent, write_intent = (intent for intent, _ in sign_bridge.actions)
    assert place_intent.place.kind == items.SIGN
    assert write_intent.write_note.object_id == "sign_9"
    assert write_intent.write_note.slot == items.SIGN_SLOT
    assert write_intent.write_note.text == "keep out"
    assert 'sign_9 now reads: "keep out"' in result.output


async def test_place_sign_says_so_when_the_writing_fails(
    sign_deps: PlannerDeps, sign_bridge: RecordingBridge
) -> None:
    sign_bridge.direct_results = [
        "place sign E -> place ok: placed sign_9 at (11, 10)",
        "write ... -> write_note failed: sign_9 is not adjacent",
    ]
    agent = build_planner_agent("test")

    with agent.override(
        model=_call_tool("place_sign", {"direction": "E", "text": "keep out"})
    ):
        result = await agent.run("go", deps=sign_deps)

    assert "still blank" in result.output
    assert "write_sign" in result.output


async def test_place_sign_stops_when_the_placement_fails(
    sign_deps: PlannerDeps, sign_bridge: RecordingBridge
) -> None:
    sign_bridge.direct_results = ["place sign E -> place failed: target occupied"]
    agent = build_planner_agent("test")

    with agent.override(
        model=_call_tool("place_sign", {"direction": "E", "text": "keep out"})
    ):
        result = await agent.run("go", deps=sign_deps)

    assert len(sign_bridge.actions) == 1
    assert "target occupied" in result.output


async def test_write_sign_submits_one_note_intent(
    sign_deps: PlannerDeps, sign_bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")

    with agent.override(
        model=_call_tool("write_sign", {"sign_id": "sign_1", "text": "all clear"})
    ):
        await agent.run("go", deps=sign_deps)

    intent, _ = sign_bridge.actions[0]
    assert intent.write_note.object_id == "sign_1"
    assert intent.write_note.text == "all clear"
    assert intent.write_note.title == ""


async def test_place_refuses_a_sign_and_points_at_place_sign(
    sign_deps: PlannerDeps, sign_bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")

    with agent.override(model=_call_tool("place", {"kind": "sign", "direction": "E"})):
        result = await agent.run("go", deps=sign_deps)

    assert sign_bridge.actions == []
    assert "place_sign" in result.output


async def test_write_sign_refuses_a_message_board(
    sign_deps: PlannerDeps, sign_bridge: RecordingBridge
) -> None:
    observe(
        sign_bridge.model,
        6,
        make_object("board_1", "message_board", (11, 10)),
        inventory={"sign": 1},
    )
    agent = build_planner_agent("test")

    with agent.override(
        model=_call_tool("write_sign", {"sign_id": "board_1", "text": "keep out"})
    ):
        result = await agent.run("go", deps=sign_deps)

    assert sign_bridge.actions == []
    assert "board_1 is a message_board, not a sign" in result.output
    assert "write_note" in result.output


async def test_write_note_refuses_a_sign(
    sign_deps: PlannerDeps, sign_bridge: RecordingBridge
) -> None:
    agent = build_planner_agent("test")

    with agent.override(
        model=_call_tool(
            "write_note",
            {"board_id": "sign_1", "slot": 0, "title": "t", "text": "keep out"},
        )
    ):
        result = await agent.run("go", deps=sign_deps)

    assert sign_bridge.actions == []
    assert "sign_1 is a sign, not a message_board" in result.output
    assert "write_sign" in result.output


class _CraftingBridge(RecordingBridge):
    """A `RecordingBridge` that folds a successful craft into its own model,
    the way the real tick loop updates `self._model` before a direct-action
    future resolves (`JevAgent._resolve_awaiting_direct` runs after
    `self._model.update`)."""

    async def direct_action(self, intent: object, description: str) -> str:
        outcome = await super().direct_action(intent, description)
        if "crafted sign" in outcome:
            observe(
                self.model,
                self.model.tick,
                sign_object("sign_1", (12, 10), "wolves north", "bob", 4),
                inventory={"wood": 0, "sign": 1},
            )
        return outcome


async def test_place_sign_crafts_one_first_when_none_is_carried(
    tmp_path: Path,
) -> None:
    model = WorldModel("ada")
    observe(
        model,
        6,
        sign_object("sign_1", (12, 10), "wolves north", "bob", 4),
        inventory={"wood": 2},
    )
    sign_bridge = _CraftingBridge(model)
    deps = PlannerDeps(bridge=sign_bridge, memory_path=tmp_path / "memory.md")
    sign_bridge.direct_results = [
        "craft sign -> craft ok: crafted sign",
        "place sign E -> place ok: placed sign_9 at (11, 10)",
        'write "keep out" on sign_9 -> write_note ok: wrote sign_9: "keep out"',
    ]
    agent = build_planner_agent("test")

    with agent.override(
        model=_call_tool("place_sign", {"direction": "E", "text": "keep out"})
    ):
        result = await agent.run("go", deps=deps)

    craft_intent, place_intent, write_intent = (
        intent for intent, _ in sign_bridge.actions
    )
    assert craft_intent.craft.recipe == "sign"
    assert place_intent.place.kind == items.SIGN
    assert write_intent.write_note.object_id == "sign_9"
    assert "sign_9 now reads" in result.output


async def test_place_sign_names_the_shortfall_with_neither_sign_nor_wood(
    sign_bridge: RecordingBridge, tmp_path: Path
) -> None:
    observe(
        sign_bridge.model, 6, sign_object("sign_1", (12, 10)), inventory={"wood": 1}
    )
    deps = PlannerDeps(bridge=sign_bridge, memory_path=tmp_path / "memory.md")
    agent = build_planner_agent("test")

    with agent.override(
        model=_call_tool("place_sign", {"direction": "E", "text": "keep out"})
    ):
        result = await agent.run("go", deps=deps)

    assert sign_bridge.actions == []
    assert "a sign takes 2 wood; you carry 1" in result.output


async def test_place_sign_validates_length_before_crafting(
    sign_bridge: RecordingBridge, tmp_path: Path
) -> None:
    observe(sign_bridge.model, 6, sign_object("sign_1", (12, 10)), inventory={})
    deps = PlannerDeps(bridge=sign_bridge, memory_path=tmp_path / "memory.md")
    agent = build_planner_agent("test")

    with agent.override(
        model=_call_tool("place_sign", {"direction": "E", "text": "x" * 81})
    ):
        await agent.run("go", deps=deps)

    assert sign_bridge.actions == [], "the length check runs before any crafting"
