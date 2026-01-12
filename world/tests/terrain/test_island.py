"""Tests for island shaping functions."""

import numpy as np
import pytest

from world.terrain.config import IslandConfig
from world.terrain.island import (
    apply_radial_falloff,
    enforce_border_ocean,
    compute_sea_level,
    create_land_mask,
)


class TestRadialFalloff:
    """Tests for radial falloff application."""

    def test_output_shape_preserved(self) -> None:
        """Output has same shape as input."""
        elevation = np.random.rand(50, 80).astype(np.float32)
        config = IslandConfig()
        result = apply_radial_falloff(elevation, config)
        assert result.shape == elevation.shape

    def test_center_less_affected(self) -> None:
        """Center of the map is less affected than edges."""
        # Uniform elevation
        elevation = np.ones((100, 100), dtype=np.float32)
        config = IslandConfig()
        result = apply_radial_falloff(elevation, config)

        center_val = result[50, 50]
        corner_val = result[0, 0]

        # Center should be higher than corner after falloff
        assert center_val > corner_val

    def test_falloff_symmetric(self) -> None:
        """Falloff is symmetric around center."""
        elevation = np.ones((100, 100), dtype=np.float32)
        config = IslandConfig()
        result = apply_radial_falloff(elevation, config)

        # Check symmetry at equidistant points from center
        assert abs(result[25, 50] - result[75, 50]) < 0.01
        assert abs(result[50, 25] - result[50, 75]) < 0.01


class TestEnforceBorderOcean:
    """Tests for border ocean enforcement."""

    def test_border_is_very_low(self) -> None:
        """Border cells have very low elevation."""
        elevation = np.ones((50, 50), dtype=np.float32)
        border_width = 3
        result = enforce_border_ocean(elevation, border_width)

        # Check top border
        assert np.all(result[:border_width, :] < 0)
        # Check bottom border
        assert np.all(result[-border_width:, :] < 0)
        # Check left border
        assert np.all(result[:, :border_width] < 0)
        # Check right border
        assert np.all(result[:, -border_width:] < 0)

    def test_interior_unchanged(self) -> None:
        """Interior cells are unchanged."""
        elevation = np.ones((50, 50), dtype=np.float32) * 5.0
        border_width = 3
        result = enforce_border_ocean(elevation, border_width)

        # Interior should still be 5.0
        interior = result[border_width:-border_width, border_width:-border_width]
        np.testing.assert_array_equal(interior, 5.0)


class TestComputeSeaLevel:
    """Tests for sea level computation."""

    def test_sea_level_achieves_target_fraction(self) -> None:
        """Sea level produces approximately target land fraction."""
        # Create elevation with known distribution
        np.random.seed(42)
        elevation = np.random.rand(100, 100).astype(np.float32)

        target_fraction = 0.7
        sea_level = compute_sea_level(elevation, target_fraction)
        land_mask = elevation >= sea_level

        actual_fraction = np.mean(land_mask)
        # Should be close to target (within 5%)
        assert abs(actual_fraction - target_fraction) < 0.05

    def test_higher_target_means_lower_sea_level(self) -> None:
        """Higher land fraction target means lower sea level."""
        elevation = np.random.rand(100, 100).astype(np.float32)

        sea_level_70 = compute_sea_level(elevation, 0.7)
        sea_level_50 = compute_sea_level(elevation, 0.5)

        # More land = lower sea level
        assert sea_level_70 < sea_level_50


class TestCreateLandMask:
    """Tests for land mask creation."""

    def test_mask_is_boolean(self) -> None:
        """Output is boolean array."""
        elevation = np.array([[0.5, 0.3], [0.8, 0.2]], dtype=np.float32)
        mask = create_land_mask(elevation, sea_level=0.4)
        assert mask.dtype == np.bool_

    def test_above_sea_level_is_land(self) -> None:
        """Cells above sea level are land."""
        elevation = np.array([[0.5, 0.3], [0.8, 0.2]], dtype=np.float32)
        mask = create_land_mask(elevation, sea_level=0.4)

        expected = np.array([[True, False], [True, False]])
        np.testing.assert_array_equal(mask, expected)

    def test_equal_to_sea_level_is_land(self) -> None:
        """Cells equal to sea level are land (>=)."""
        elevation = np.array([[0.4]], dtype=np.float32)
        mask = create_land_mask(elevation, sea_level=0.4)
        assert mask[0, 0] == True
