"""Tests for terrain classification functions."""

import numpy as np
import pytest

from world.terrain_types import FloorType
from world.terrain.classification import (
    _floor_value,
    floor_value_to_type,
    classify_terrain,
)
from world.terrain.config import ClassificationConfig


class TestFloorValueConversion:
    """Tests for FloorType <-> uint8 conversion."""

    def test_floor_value_mapping(self) -> None:
        """Each FloorType maps to a unique uint8."""
        values = [_floor_value(ft) for ft in FloorType]
        assert len(values) == len(set(values)), "Duplicate values in mapping"

    def test_floor_value_range(self) -> None:
        """Floor values are in valid uint8 range."""
        for ft in FloorType:
            value = _floor_value(ft)
            assert 0 <= value <= 255

    def test_round_trip_conversion(self) -> None:
        """floor_value_to_type inverts _floor_value."""
        for ft in FloorType:
            value = _floor_value(ft)
            recovered = floor_value_to_type(value)
            assert recovered == ft

    def test_unknown_value_returns_grass(self) -> None:
        """Unknown values default to GRASS."""
        result = floor_value_to_type(99)
        assert result == FloorType.GRASS

    def test_specific_values(self) -> None:
        """Check specific known values."""
        assert _floor_value(FloorType.DEEP_WATER) == 0
        assert _floor_value(FloorType.SHALLOW_WATER) == 1
        assert _floor_value(FloorType.SAND) == 2
        assert _floor_value(FloorType.GRASS) == 3
        assert _floor_value(FloorType.DIRT) == 4
        assert _floor_value(FloorType.MOUNTAIN) == 5
        assert _floor_value(FloorType.STONE) == 6


class TestClassifyTerrain:
    """Tests for terrain classification."""

    @pytest.fixture
    def simple_inputs(self) -> dict:
        """Create simple test inputs for classification."""
        height, width = 20, 20

        # Half land (right side), half water (left side)
        land_mask = np.zeros((height, width), dtype=bool)
        land_mask[:, 10:] = True

        return {
            "land_mask": land_mask,
            "river_mask": np.zeros((height, width), dtype=bool),
            "ford_mask": np.zeros((height, width), dtype=bool),
            "elevation": np.random.rand(height, width).astype(np.float32),
            "moisture": np.ones((height, width), dtype=np.float32) * 0.5,
            "slope": np.zeros((height, width), dtype=np.float32),
            "dist_to_water": np.zeros((height, width), dtype=np.float32),
            "dist_to_land": np.zeros((height, width), dtype=np.float32),
            "beach_noise": np.ones((height, width), dtype=np.float32) * 0.5,
            "shallow_noise": np.ones((height, width), dtype=np.float32) * 0.5,
            "ridged_noise": np.zeros((height, width), dtype=np.float32),
            "config": ClassificationConfig(),
        }

    def test_output_shape(self, simple_inputs: dict) -> None:
        """Output has same shape as input."""
        floor = classify_terrain(**simple_inputs)
        assert floor.shape == simple_inputs["land_mask"].shape

    def test_output_dtype(self, simple_inputs: dict) -> None:
        """Output is uint8."""
        floor = classify_terrain(**simple_inputs)
        assert floor.dtype == np.uint8

    def test_ocean_is_water(self, simple_inputs: dict) -> None:
        """Ocean cells (not land) are water."""
        floor = classify_terrain(**simple_inputs)

        # Left side is ocean
        ocean_floor = floor[:, :10]
        deep_water_val = _floor_value(FloorType.DEEP_WATER)
        shallow_water_val = _floor_value(FloorType.SHALLOW_WATER)

        # All ocean should be some kind of water
        for y in range(20):
            for x in range(10):
                assert floor[y, x] in {deep_water_val, shallow_water_val}

    def test_river_is_deep_water(self, simple_inputs: dict) -> None:
        """River cells are deep water."""
        simple_inputs["river_mask"][10, 15] = True  # River on land
        # Set dist_to_water high so beach doesn't overwrite river
        simple_inputs["dist_to_water"][10, 15] = 100.0
        # Set elevation low so it doesn't become mountain
        simple_inputs["elevation"][10, 15] = 0.1
        floor = classify_terrain(**simple_inputs)

        deep_water_val = _floor_value(FloorType.DEEP_WATER)
        assert floor[10, 15] == deep_water_val

    def test_ford_is_shallow_water(self, simple_inputs: dict) -> None:
        """Ford cells are shallow water."""
        simple_inputs["ford_mask"][10, 15] = True  # Ford on land
        # Set dist_to_water high so beach doesn't overwrite ford
        simple_inputs["dist_to_water"][10, 15] = 100.0
        floor = classify_terrain(**simple_inputs)

        shallow_water_val = _floor_value(FloorType.SHALLOW_WATER)
        assert floor[10, 15] == shallow_water_val

    def test_low_moisture_is_dirt(self, simple_inputs: dict) -> None:
        """Low moisture areas become dirt."""
        # Set low moisture on a patch of land
        simple_inputs["moisture"][5:10, 15:18] = 0.1

        # Make sure these cells aren't beach or mountain
        simple_inputs["dist_to_water"][5:10, 15:18] = 100.0  # Far from water (not beach)
        simple_inputs["ridged_noise"][5:10, 15:18] = 0.0  # Not ridged
        simple_inputs["elevation"][5:10, 15:18] = 0.1  # Low elevation

        floor = classify_terrain(**simple_inputs)

        dirt_val = _floor_value(FloorType.DIRT)
        # At least some of this area should be dirt
        dirt_count = np.sum(floor[5:10, 15:18] == dirt_val)
        assert dirt_count > 0

    def test_valid_floor_values(self, simple_inputs: dict) -> None:
        """All output values are valid FloorType values."""
        floor = classify_terrain(**simple_inputs)

        valid_values = {_floor_value(ft) for ft in FloorType}
        unique_values = set(np.unique(floor))
        assert unique_values.issubset(valid_values)
