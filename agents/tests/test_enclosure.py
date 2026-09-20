"""Enclosure geometry: seals, rooms, the enclosed fact and own pieces.

Every case here comes from the six-settler run `20260920-030501-hamlet`: esme
walled the six free neighbours of her own tile and starved in the cell, cleo
built a 3x3 ring with no door and believed a bed was inside it, and neither
planner was ever told what its walls had actually made.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from agents.jev_agent import items
from agents.jev_agent.enclosure import (
    ENCLOSED_REACH_LIMIT,
    build_geometry_lines,
    enclosed_fact,
    own_pieces_line,
    reach_count,
    rooms_around,
    would_seal,
)
from agents.jev_agent.options import enumerate_options
from agents.jev_agent.worldmodel import WorldModel

from helpers import make_entity, make_object, make_observation

Coord = tuple[int, int]


def model_with(
    position: Coord = (10, 10),
    *,
    walls: Sequence[Coord] = (),
    doors: Sequence[Coord] = (),
    inventory: Mapping[str, int] | None = None,
    owner: str = "ada",
    extra: Sequence[object] = (),
) -> WorldModel:
    """`ada` in an open field, with wall and door objects where asked."""
    objects = [
        make_object(f"wood_wall_{index}", items.WOOD_WALL, tile, {"owner": owner})
        for index, tile in enumerate(walls)
    ]
    objects += [
        make_object(f"door_{index}", items.DOOR, tile, {"owner": owner})
        for index, tile in enumerate(doors)
    ]
    objects += list(extra)  # type: ignore[arg-type]
    model = WorldModel("ada")
    model.update(
        make_observation(
            1,
            make_entity("ada", position, inventory=dict(inventory or {})),
            objects=objects,  # type: ignore[arg-type]
        )
    )
    return model


def ring_around(centre: Coord, radius: int = 1) -> list[Coord]:
    """The square ring of tiles `radius` out from `centre`."""
    x, y = centre
    return [
        (x + dx, y + dy)
        for dx in range(-radius, radius + 1)
        for dy in range(-radius, radius + 1)
        if max(abs(dx), abs(dy)) == radius
    ]


class TestReach:
    def test_open_ground_reaches_the_cap(self) -> None:
        model = model_with()
        assert reach_count(model) == ENCLOSED_REACH_LIMIT

    def test_a_one_tile_cell_reaches_one_tile(self) -> None:
        model = model_with(walls=ring_around((10, 10)))
        assert reach_count(model) == 1

    def test_a_door_in_the_ring_is_a_way_out(self) -> None:
        ring = ring_around((10, 10))
        gate = (10, 9)
        model = model_with(walls=[tile for tile in ring if tile != gate], doors=[gate])
        assert reach_count(model) == ENCLOSED_REACH_LIMIT

    def test_a_corner_gap_is_not_a_way_out(self) -> None:
        """The diagonal rule: both orthogonal components must be passable."""
        ring = ring_around((10, 10))
        model = model_with(walls=[tile for tile in ring if tile != (9, 9)])
        assert reach_count(model) == 1


class TestEnclosedFact:
    def test_open_ground_says_nothing(self) -> None:
        assert enclosed_fact(model_with()) == ""

    def test_the_cell_names_the_pieces_around_it(self) -> None:
        model = model_with(walls=ring_around((10, 10)))
        fact = enclosed_fact(model)
        assert fact.startswith("!! ENCLOSED: you can reach only 1 tile(s)")
        assert "wood_wall_0 (NW)" in fact or "wood_wall_0 (N)" in fact
        assert "dismantle removes a placed piece" in fact
        # Physics, not advice.
        assert "should" not in fact

    def test_a_dead_body_gets_no_fact(self) -> None:
        model = WorldModel("ada")
        model.update(
            make_observation(
                1,
                make_entity("ada", (10, 10), alive=False),
                objects=[  # type: ignore[arg-type]
                    make_object(f"wood_wall_{i}", items.WOOD_WALL, tile)
                    for i, tile in enumerate(ring_around((10, 10)))
                ],
            )
        )
        assert enclosed_fact(model) == ""


class TestSealGuard:
    def test_the_last_free_neighbour_would_seal(self) -> None:
        ring = ring_around((10, 10))
        model = model_with(walls=ring[1:])
        assert would_seal(model, (10, 10), ring[0]) is True

    def test_an_open_field_placement_does_not_seal(self) -> None:
        model = model_with()
        assert would_seal(model, (10, 10), (10, 9)) is False

    def test_jev_is_not_offered_the_placement_that_shuts_it_in(self) -> None:
        """esme's cell: walling the last free neighbour is no longer an option."""
        ring = ring_around((10, 10))
        model = model_with(walls=ring[1:], inventory={items.WOOD_WALL: 2})
        keys = {option.key for option in enumerate_options(model)}
        assert not any(key.startswith(f"place:{items.WOOD_WALL}:") for key in keys)

    def test_jev_may_still_place_a_wall_in_the_open(self) -> None:
        model = model_with(inventory={items.WOOD_WALL: 2})
        keys = {option.key for option in enumerate_options(model)}
        assert any(key.startswith(f"place:{items.WOOD_WALL}:") for key in keys)

    def test_a_door_is_never_a_seal(self) -> None:
        ring = ring_around((10, 10))
        model = model_with(walls=ring[1:], inventory={items.DOOR: 1})
        keys = {option.key for option in enumerate_options(model)}
        assert any(key.startswith(f"place:{items.DOOR}:") for key in keys)


class TestRooms:
    def test_a_closed_ring_has_one_interior_tile(self) -> None:
        ring = ring_around((10, 10))
        model = model_with(position=(14, 14), walls=ring)
        rooms = rooms_around(model, ring)
        assert [len(room.tiles) for room in rooms] == [1]
        assert rooms[0].doors == 0
        assert rooms[0].span == "1x1"

    def test_a_ring_with_a_gap_encloses_nothing(self) -> None:
        ring = ring_around((10, 10))
        model = model_with(
            position=(14, 14), walls=[tile for tile in ring if tile != (10, 9)]
        )
        assert rooms_around(model, ring) == []

    def test_a_corner_only_ring_leaks(self) -> None:
        """Four corners of a 3x3 do not enclose the middle: fills are 4-connected."""
        corners = [(9, 9), (11, 9), (9, 11), (11, 11)]
        model = model_with(position=(14, 14), walls=corners)
        assert rooms_around(model, corners) == []

    def test_a_door_counts_as_boundary_and_as_an_entrance(self) -> None:
        ring = ring_around((10, 10), radius=2)
        gate = (10, 8)
        model = model_with(
            position=(14, 14),
            walls=[tile for tile in ring if tile != gate],
            doors=[gate],
        )
        rooms = rooms_around(model, ring)
        assert len(rooms) == 1
        assert len(rooms[0].tiles) == 9
        assert rooms[0].doors == 1


class TestBuildGeometryLines:
    def test_a_closed_ring_states_the_interior_and_which_side_you_are_on(
        self,
    ) -> None:
        """cleo's 3x3: the result used to say nothing about the interior."""
        ring = ring_around((10, 10), radius=1)
        model = model_with(position=(13, 13), walls=ring)
        text = "\n".join(build_geometry_lines(model, ring))
        assert "enclose 1 interior tile(s) spanning 1x1" in text
        assert "doors: 0" in text
        assert "gaps: 0" in text
        assert "you are outside" in text
        assert "the interior has no entrance: no door and no gap" in text

    def test_the_seal_guard_note_says_why_you_are_outside(self) -> None:
        ring = ring_around((10, 10), radius=1)
        model = model_with(position=(13, 13), walls=ring)
        text = "\n".join(build_geometry_lines(model, ring, stood_outside=True))
        assert "placing it from inside would have shut you in" in text

    def test_standing_inside_says_inside(self) -> None:
        ring = ring_around((10, 10), radius=2)
        model = model_with(position=(10, 10), walls=ring)
        text = "\n".join(build_geometry_lines(model, ring))
        assert "you are inside" in text
        assert "spanning 3x3" in text

    def test_an_unfinished_ring_names_its_gaps(self) -> None:
        ring = ring_around((10, 10), radius=1)
        standing = [tile for tile in ring if tile not in {(9, 10), (10, 9)}]
        model = model_with(position=(13, 13), walls=standing)
        text = "\n".join(build_geometry_lines(model, ring))
        assert "these walls enclose nothing yet: 2 gap(s) remain at" in text
        assert "(9, 10)" in text and "(10, 9)" in text

    def test_refused_tiles_are_named(self) -> None:
        ring = ring_around((10, 10), radius=1)
        gate = (10, 9)
        model = model_with(
            position=(13, 13), walls=[tile for tile in ring if tile != gate]
        )
        text = "\n".join(build_geometry_lines(model, ring, refused=[gate]))
        assert "refused as sealing you in" in text
        assert "(10, 9)" in text


class TestOwnPieces:
    def test_nothing_placed_says_nothing(self) -> None:
        assert own_pieces_line(model_with()) == ""

    def test_one_cluster_is_one_bounding_box(self) -> None:
        model = model_with(walls=[(20, 20), (21, 20), (22, 20), (22, 21)])
        line = own_pieces_line(model)
        assert line == ("your placed pieces: 4 wood_wall within (20, 20)-(22, 21)")

    def test_a_lone_piece_gets_its_tile(self) -> None:
        model = model_with(
            walls=(),
            extra=[make_object("bed_1", items.BED, (30, 30), {"owner": "ada"})],
        )
        assert own_pieces_line(model) == "your placed pieces: 1 bed at (30, 30)"

    def test_separate_clusters_are_listed_apart(self) -> None:
        model = model_with(walls=[(20, 20), (21, 20), (40, 40)])
        line = own_pieces_line(model)
        assert "2 wood_wall within (20, 20)-(21, 20)" in line
        assert "1 wood_wall at (40, 40)" in line

    def test_somebody_elses_wall_is_not_yours(self) -> None:
        model = model_with(walls=[(20, 20)], owner="cleo")
        assert own_pieces_line(model) == ""

    def test_the_block_is_capped(self) -> None:
        tiles = [(20 + 5 * index, 20) for index in range(12)]
        line = own_pieces_line(model_with(walls=tiles), limit=3)
        assert line.endswith("and 9 more group(s)")
