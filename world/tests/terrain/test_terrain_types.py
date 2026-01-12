"""Tests for FloorType enum."""

import pytest

from world.terrain_types import FloorType


class TestFloorTypeWalkability:
    """Test walkability property of floor types."""

    def test_deep_water_not_walkable(self) -> None:
        assert not FloorType.DEEP_WATER.walkable

    def test_shallow_water_walkable(self) -> None:
        assert FloorType.SHALLOW_WATER.walkable

    def test_sand_walkable(self) -> None:
        assert FloorType.SAND.walkable

    def test_grass_walkable(self) -> None:
        assert FloorType.GRASS.walkable

    def test_dirt_walkable(self) -> None:
        assert FloorType.DIRT.walkable

    def test_mountain_not_walkable(self) -> None:
        assert not FloorType.MOUNTAIN.walkable

    def test_stone_walkable(self) -> None:
        assert FloorType.STONE.walkable


class TestFloorTypeOpacity:
    """Test opacity property of floor types."""

    def test_mountain_opaque(self) -> None:
        assert FloorType.MOUNTAIN.opaque

    def test_water_not_opaque(self) -> None:
        assert not FloorType.DEEP_WATER.opaque
        assert not FloorType.SHALLOW_WATER.opaque

    def test_land_types_not_opaque(self) -> None:
        assert not FloorType.SAND.opaque
        assert not FloorType.GRASS.opaque
        assert not FloorType.DIRT.opaque
        assert not FloorType.STONE.opaque


class TestFloorTypeValues:
    """Test FloorType enum string values."""

    def test_values_are_strings(self) -> None:
        for floor_type in FloorType:
            assert isinstance(floor_type.value, str)

    def test_expected_values(self) -> None:
        assert FloorType.DEEP_WATER.value == "deep_water"
        assert FloorType.SHALLOW_WATER.value == "shallow_water"
        assert FloorType.SAND.value == "sand"
        assert FloorType.GRASS.value == "grass"
        assert FloorType.DIRT.value == "dirt"
        assert FloorType.MOUNTAIN.value == "mountain"
        assert FloorType.STONE.value == "stone"
