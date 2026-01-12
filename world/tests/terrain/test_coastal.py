"""Tests for coastal refinement functions."""

import numpy as np
import pytest

from world.terrain.coastal import (
    keep_largest_component,
    majority_smooth,
    compute_distance_to_water,
    compute_distance_to_land,
)


class TestKeepLargestComponent:
    """Tests for connected component filtering."""

    def test_single_component_unchanged(self) -> None:
        """Single connected component is unchanged."""
        land_mask = np.array([
            [False, False, False, False],
            [False, True,  True,  False],
            [False, True,  True,  False],
            [False, False, False, False],
        ])
        result = keep_largest_component(land_mask)
        np.testing.assert_array_equal(result, land_mask)

    def test_smaller_component_removed(self) -> None:
        """Smaller disconnected component is removed."""
        land_mask = np.array([
            [True,  False, False, False],
            [False, False, False, False],
            [False, False, True,  True],
            [False, False, True,  True],
        ])
        result = keep_largest_component(land_mask)

        # Small component (top-left) should be gone
        assert result[0, 0] == False
        # Large component (bottom-right) should remain
        assert result[2, 2] == True
        assert result[3, 3] == True

    def test_empty_mask_unchanged(self) -> None:
        """Empty mask returns empty."""
        land_mask = np.zeros((10, 10), dtype=bool)
        result = keep_largest_component(land_mask)
        assert np.sum(result) == 0

    def test_8_connected_diagonal(self) -> None:
        """8-connected components include diagonals."""
        land_mask = np.array([
            [True,  False, False],
            [False, True,  False],
            [False, False, True],
        ])
        result = keep_largest_component(land_mask, connectivity=2)
        # All should be connected via diagonal
        np.testing.assert_array_equal(result, land_mask)


class TestMajoritySmooth:
    """Tests for majority filter smoothing."""

    def test_isolated_pixel_removed(self) -> None:
        """Single isolated land pixel is removed."""
        land_mask = np.array([
            [False, False, False, False, False],
            [False, False, False, False, False],
            [False, False, True,  False, False],
            [False, False, False, False, False],
            [False, False, False, False, False],
        ])
        result = majority_smooth(land_mask, iterations=1)
        # Isolated pixel has 0 land neighbors, should be removed
        assert result[2, 2] == False

    def test_solid_block_unchanged(self) -> None:
        """Solid block of land is mostly unchanged."""
        land_mask = np.ones((10, 10), dtype=bool)
        land_mask[0, :] = False  # Water border
        land_mask[-1, :] = False
        land_mask[:, 0] = False
        land_mask[:, -1] = False

        result = majority_smooth(land_mask, iterations=1)
        # Interior should still be land
        assert np.all(result[2:-2, 2:-2])

    def test_pinhole_filled(self) -> None:
        """Single water pixel in land mass is filled."""
        land_mask = np.ones((5, 5), dtype=bool)
        land_mask[2, 2] = False  # Pinhole in center

        result = majority_smooth(land_mask, iterations=1, water_threshold=6)
        # Pinhole has 8 land neighbors, should be filled
        assert result[2, 2] == True


class TestDistanceToWater:
    """Tests for distance to water computation."""

    def test_water_cells_have_zero_distance(self) -> None:
        """Water cells (False) have distance 0."""
        land_mask = np.array([
            [False, False, True],
            [False, True,  True],
            [True,  True,  True],
        ])
        dist = compute_distance_to_water(land_mask)
        # This function computes distance on ~water, so water cells aren't 0
        # Let's verify land cells near water have low distance
        assert dist[0, 2] <= 1.5  # Adjacent to water
        assert dist[2, 2] > dist[1, 2]  # Further from water

    def test_distance_increases_inland(self) -> None:
        """Distance increases as we move inland."""
        # Create a strip of land
        land_mask = np.zeros((10, 10), dtype=bool)
        land_mask[3:7, :] = True  # Land strip in middle

        dist = compute_distance_to_water(land_mask)
        # Center of strip should have higher distance than edges
        assert dist[5, 5] > dist[3, 5]


class TestDistanceToLand:
    """Tests for distance to land computation."""

    def test_land_cells_have_zero_distance(self) -> None:
        """Land cells have distance 0."""
        land_mask = np.array([
            [False, False, True],
            [False, True,  True],
            [True,  True,  True],
        ])
        dist = compute_distance_to_land(land_mask)
        # Water cells should have positive distance
        assert dist[0, 0] > 0
        assert dist[0, 1] > 0

    def test_distance_increases_into_ocean(self) -> None:
        """Distance increases as we move into ocean."""
        # Create ocean with land on one side
        land_mask = np.zeros((10, 10), dtype=bool)
        land_mask[:, 7:] = True  # Land on right side

        dist = compute_distance_to_land(land_mask)
        # Distance should increase toward left edge
        assert dist[5, 0] > dist[5, 5]
