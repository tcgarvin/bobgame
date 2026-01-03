"""Tests for proto conversion functions."""

import pytest

from world import world_pb2 as pb
from world.conversion import (
    direction_from_proto,
    direction_to_proto,
    entity_from_proto,
    entity_to_proto,
    position_from_proto,
    position_to_proto,
    tile_from_proto,
    tile_to_proto,
)
from world.state import Entity, Tile
from world.types import Direction, Position


class TestDirectionConversion:
    """Tests for Direction conversion."""

    def test_direction_to_proto_north(self) -> None:
        result = direction_to_proto(Direction.NORTH)
        assert result == pb.NORTH

    def test_direction_to_proto_all_directions(self) -> None:
        for direction in Direction:
            proto_dir = direction_to_proto(direction)
            assert proto_dir == direction.value

    def test_direction_from_proto_north(self) -> None:
        result = direction_from_proto(pb.NORTH)
        assert result == Direction.NORTH

    def test_direction_from_proto_unspecified(self) -> None:
        result = direction_from_proto(pb.DIRECTION_UNSPECIFIED)
        assert result is None

    def test_direction_roundtrip(self) -> None:
        for direction in Direction:
            proto = direction_to_proto(direction)
            back = direction_from_proto(proto)
            assert back == direction


class TestPositionConversion:
    """Tests for Position conversion."""

    def test_position_to_proto(self) -> None:
        pos = Position(x=5, y=10)
        proto = position_to_proto(pos)

        assert proto.x == 5
        assert proto.y == 10

    def test_position_from_proto(self) -> None:
        proto = pb.Position(x=3, y=7)
        pos = position_from_proto(proto)

        assert pos.x == 3
        assert pos.y == 7

    def test_position_roundtrip(self) -> None:
        original = Position(x=42, y=99)
        proto = position_to_proto(original)
        back = position_from_proto(proto)

        assert back == original


class TestEntityConversion:
    """Tests for Entity conversion."""

    def test_entity_to_proto_basic(self) -> None:
        entity = Entity(
            entity_id="test-entity",
            position=Position(x=5, y=10),
            entity_type="player",
        )
        proto = entity_to_proto(entity)

        assert proto.entity_id == "test-entity"
        assert proto.position.x == 5
        assert proto.position.y == 10
        assert proto.entity_type == "player"

    def test_entity_to_proto_with_tags(self) -> None:
        entity = Entity(
            entity_id="test-entity",
            position=Position(x=0, y=0),
            tags=("npc", "friendly"),
        )
        proto = entity_to_proto(entity)

        assert list(proto.tags) == ["npc", "friendly"]

    def test_entity_to_proto_with_status_bits(self) -> None:
        entity = Entity(
            entity_id="test-entity",
            position=Position(x=0, y=0),
            status_bits=0b1010,
        )
        proto = entity_to_proto(entity)

        assert proto.status_bits == 0b1010

    def test_entity_from_proto(self) -> None:
        proto = pb.Entity(
            entity_id="test-entity",
            position=pb.Position(x=5, y=10),
            entity_type="monster",
            tags=["hostile"],
            status_bits=0b0101,
        )
        entity = entity_from_proto(proto)

        assert entity.entity_id == "test-entity"
        assert entity.position == Position(x=5, y=10)
        assert entity.entity_type == "monster"
        assert entity.tags == ("hostile",)
        assert entity.status_bits == 0b0101

    def test_entity_roundtrip(self) -> None:
        original = Entity(
            entity_id="roundtrip-entity",
            position=Position(x=7, y=3),
            entity_type="npc",
            tags=("merchant", "friendly"),
            status_bits=42,
        )
        proto = entity_to_proto(original)
        back = entity_from_proto(proto)

        assert back.entity_id == original.entity_id
        assert back.position == original.position
        assert back.entity_type == original.entity_type
        assert back.tags == original.tags
        assert back.status_bits == original.status_bits


class TestTileConversion:
    """Tests for Tile conversion."""

    def test_tile_to_proto_default(self) -> None:
        tile = Tile(position=Position(x=0, y=0))
        proto = tile_to_proto(tile)

        assert proto.position.x == 0
        assert proto.position.y == 0
        assert proto.walkable is True
        assert proto.opaque is False
        assert proto.floor_type == "stone"

    def test_tile_to_proto_wall(self) -> None:
        tile = Tile(
            position=Position(x=5, y=5),
            walkable=False,
            opaque=True,
            floor_type="brick",
        )
        proto = tile_to_proto(tile)

        assert proto.walkable is False
        assert proto.opaque is True
        assert proto.floor_type == "brick"

    def test_tile_from_proto(self) -> None:
        proto = pb.Tile(
            position=pb.Position(x=3, y=7),
            walkable=False,
            opaque=True,
            floor_type="wood",
        )
        tile = tile_from_proto(proto)

        assert tile.position == Position(x=3, y=7)
        assert tile.walkable is False
        assert tile.opaque is True
        assert tile.floor_type == "wood"

    def test_tile_roundtrip(self) -> None:
        original = Tile(
            position=Position(x=10, y=20),
            walkable=True,
            opaque=False,
            floor_type="grass",
        )
        proto = tile_to_proto(original)
        back = tile_from_proto(proto)

        assert back.position == original.position
        assert back.walkable == original.walkable
        assert back.opaque == original.opaque
        assert back.floor_type == original.floor_type
