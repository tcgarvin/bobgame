"""The build tool: shape geometry, and the driver that walks and places."""

from __future__ import annotations

from typing import Mapping, Sequence

import pytest

from agents.jev_agent.build import (
    BUILD_BLOCKED,
    BUILD_DANGER,
    BUILD_DONE,
    BUILD_OUT_OF_ITEMS,
    BUILD_WOULD_SEAL,
    BuildExecutor,
    BuildPlanError,
    BuildStateError,
    line_tiles,
    make_plan,
    plan_tiles,
    rect_filled_tiles,
    rect_outline_tiles,
)
from agents.jev_agent.stint import DriverChoice
from agents.jev_agent.worldmodel import WorldModel

from helpers import make_entity, make_object, make_observation

Coord = tuple[int, int]


def build_model(
    position: Coord = (10, 10),
    *,
    inventory: Mapping[str, int] | None = None,
    objects: Sequence[object] = (),
    entities: Sequence[object] = (),
    health: int = 20,
) -> WorldModel:
    """A world model for `ada` after one observation of an open field."""
    model = WorldModel("ada")
    model.update(
        make_observation(
            1,
            make_entity(
                "ada", position, inventory=dict(inventory or {}), health=health
            ),
            objects=list(objects),  # type: ignore[arg-type]
            entities=list(entities),  # type: ignore[arg-type]
        )
    )
    return model


def step(executor: BuildExecutor, model: WorldModel) -> tuple[str, str]:
    """Drive one tick the way the stint does: stop check first, then choose."""
    reason = executor.stop_reason(model)
    if reason:
        return (reason, "")
    choice: DriverChoice = executor.choose(model)
    return ("", choice.option.key)


# --- geometry ---------------------------------------------------------------


def test_line_tiles_covers_both_ends_without_gaps() -> None:
    tiles = line_tiles((3, 3), (7, 3))
    assert tiles == [(3, 3), (4, 3), (5, 3), (6, 3), (7, 3)]


def test_line_tiles_walks_a_diagonal_one_tile_at_a_time() -> None:
    tiles = line_tiles((0, 0), (3, 3))
    assert tiles == [(0, 0), (1, 1), (2, 2), (3, 3)]


def test_line_tiles_of_a_single_point_is_that_point() -> None:
    assert line_tiles((4, 4), (4, 4)) == [(4, 4)]


def test_rect_outline_is_the_perimeter_once_each() -> None:
    tiles = rect_outline_tiles((0, 0), (2, 2))
    assert len(tiles) == 8
    assert (1, 1) not in tiles
    assert tiles[0] == (0, 0)
    assert len(set(tiles)) == len(tiles)


def test_rect_filled_covers_every_tile() -> None:
    tiles = rect_filled_tiles((0, 0), (2, 1))
    assert sorted(tiles) == [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1)]


def test_plan_tiles_drops_the_door_gap() -> None:
    tiles = plan_tiles("rect", (0, 0), (2, 2), skip=[(1, 0)])
    assert (1, 0) not in tiles
    assert len(tiles) == 7


def test_plan_tiles_rejects_an_unknown_shape() -> None:
    with pytest.raises(BuildPlanError):
        plan_tiles("circle", (0, 0), (1, 1))


def test_plan_tiles_rejects_an_empty_explicit_list() -> None:
    with pytest.raises(BuildPlanError):
        plan_tiles("tiles", (0, 0), (1, 1))


def test_plan_tiles_rejects_a_plan_that_is_far_too_big() -> None:
    with pytest.raises(BuildPlanError):
        plan_tiles("rect_filled", (0, 0), (40, 40))


def test_make_plan_rejects_something_that_is_not_a_building_item() -> None:
    with pytest.raises(BuildPlanError):
        make_plan("berry", "line", (0, 0), (1, 0))


def test_make_plan_knows_ground_kinds_from_structures() -> None:
    assert make_plan("road", "line", (0, 0), (1, 0)).is_ground
    assert not make_plan("wood_wall", "line", (0, 0), (1, 0)).is_ground
    assert make_plan("wood_wall", "line", (0, 0), (1, 0)).blocks
    assert not make_plan("door", "line", (0, 0), (1, 0)).blocks


# --- the driver -------------------------------------------------------------


def test_ground_pieces_are_laid_on_the_tile_you_stand_on() -> None:
    plan = make_plan("road", "line", (10, 10), (12, 10))
    executor = BuildExecutor(plan)
    model = build_model(inventory={"road": 3})
    assert step(executor, model) == ("", "build_place:road:10,10")


def test_the_builder_walks_to_the_next_ground_tile() -> None:
    plan = make_plan("road", "line", (10, 10), (12, 10))
    executor = BuildExecutor(plan)
    model = build_model(
        inventory={"road": 3}, objects=[make_object("r1", "road", (10, 10))]
    )
    assert step(executor, model) == ("", "build_step:E")


def test_structures_are_placed_from_an_adjacent_tile() -> None:
    plan = make_plan("wood_wall", "line", (11, 10), (11, 10))
    executor = BuildExecutor(plan)
    model = build_model(inventory={"wood_wall": 1})
    reason, key = step(executor, model)
    assert reason == ""
    assert key == "build_place:wood_wall:11,10"
    choice = executor.choose(model)
    assert choice.option.intent.place.kind == "wood_wall"
    assert choice.option.intent.place.direction != 0


def test_a_finished_plan_stops_with_build_done() -> None:
    plan = make_plan("road", "line", (10, 10), (11, 10))
    executor = BuildExecutor(plan)
    model = build_model(
        inventory={"road": 3},
        objects=[
            make_object("r1", "road", (10, 10)),
            make_object("r2", "road", (11, 10)),
        ],
    )
    assert executor.stop_reason(model) == BUILD_DONE
    assert len(executor.progress().already_there) == 2


def test_an_empty_pack_stops_the_build() -> None:
    plan = make_plan("wood_wall", "line", (12, 10), (12, 12))
    executor = BuildExecutor(plan)
    model = build_model(inventory={"wood": 4})
    assert executor.stop_reason(model) == BUILD_OUT_OF_ITEMS


def test_a_wolf_next_door_stops_the_build() -> None:
    plan = make_plan("road", "line", (10, 10), (12, 10))
    executor = BuildExecutor(plan)
    model = build_model(
        inventory={"road": 3},
        entities=[make_entity("wolf_1", (11, 11), entity_type="wolf")],
    )
    assert executor.stop_reason(model) == BUILD_DANGER


def test_being_badly_hurt_stops_the_build() -> None:
    plan = make_plan("road", "line", (10, 10), (12, 10))
    executor = BuildExecutor(plan)
    model = build_model(inventory={"road": 3}, health=3)
    assert executor.stop_reason(model) == BUILD_DANGER


def test_tiles_that_already_hold_something_else_are_skipped() -> None:
    plan = make_plan("road", "line", (10, 10), (12, 10))
    executor = BuildExecutor(plan)
    model = build_model(
        inventory={"road": 3}, objects=[make_object("t1", "tree", (11, 10))]
    )
    executor.stop_reason(model)
    progress = executor.progress()
    assert (11, 10) not in progress.remaining
    assert progress.skipped[0][0] == (11, 10)
    assert "tree" in progress.skipped[0][1]
    assert "tree" in executor.summary()


def test_a_structure_tile_blocked_by_a_wall_is_skipped() -> None:
    plan = make_plan("wood_wall", "line", (12, 10), (12, 10))
    executor = BuildExecutor(plan)
    model = build_model(
        inventory={"wood_wall": 2},
        objects=[make_object("w1", "stone_wall", (12, 10))],
    )
    assert executor.stop_reason(model) == BUILD_DONE
    assert executor.progress().skipped[0][0] == (12, 10)


def test_an_unreachable_tile_reports_blocked() -> None:
    walls = [
        make_object(f"w{index}", "wood_wall", position)
        for index, position in enumerate(
            [(4, 4), (5, 4), (6, 4), (4, 5), (6, 5), (4, 6), (5, 6), (6, 6)]
        )
    ]
    plan = make_plan("road", "line", (5, 5), (5, 5))
    executor = BuildExecutor(plan)
    model = build_model(inventory={"road": 2}, objects=walls)
    assert executor.stop_reason(model) == BUILD_BLOCKED


def ring_walls(gap: Coord) -> list[object]:
    """A 3x3 wall ring around (10, 10) with one tile left open."""
    perimeter = rect_outline_tiles((9, 9), (11, 11))
    return [
        make_object(f"w{index}", "wood_wall", tile)
        for index, tile in enumerate(perimeter)
        if tile != gap
    ]


def test_the_last_wall_of_a_ring_is_placed_from_outside() -> None:
    gap: Coord = (11, 10)
    plan = make_plan("wood_wall", "tiles", (0, 0), (0, 0), explicit=[gap])
    executor = BuildExecutor(plan)
    model = build_model(inventory={"wood_wall": 1}, objects=ring_walls(gap))
    reason, key = step(executor, model)
    # Standing in the middle would shut the builder in, so it walks out first.
    assert reason == ""
    assert key.startswith("build_step:")


def test_a_ring_with_no_way_out_stops_rather_than_sealing_you_in() -> None:
    # The only gap is diagonal, so the builder cannot leave through it and
    # every way of closing it traps it inside.
    gap: Coord = (11, 11)
    plan = make_plan("wood_wall", "tiles", (0, 0), (0, 0), explicit=[gap])
    executor = BuildExecutor(plan)
    model = build_model(inventory={"wood_wall": 1}, objects=ring_walls(gap))
    assert executor.stop_reason(model) == BUILD_WOULD_SEAL


def test_choosing_after_the_build_is_over_raises() -> None:
    plan = make_plan("road", "line", (10, 10), (10, 10))
    executor = BuildExecutor(plan)
    model = build_model(
        inventory={"road": 1}, objects=[make_object("r1", "road", (10, 10))]
    )
    assert executor.stop_reason(model) == BUILD_DONE
    with pytest.raises(BuildStateError):
        executor.choose(model)


def test_summary_counts_what_happened() -> None:
    plan = make_plan("road", "line", (10, 10), (12, 10))
    executor = BuildExecutor(plan)
    model = build_model(inventory={"road": 3})
    executor.stop_reason(model)
    text = executor.summary()
    assert "BUILD road" in text
    assert "left to do: 3" in text
