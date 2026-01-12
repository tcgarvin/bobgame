"""Tests for hydrology functions."""

import numpy as np
import pytest

from world.terrain.hydrology import (
    compute_d8_flow_direction,
    compute_flow_accumulation,
    priority_flood_fill,
    FLOW_NODATA,
    D8_DY,
    D8_DX,
)


class TestD8FlowDirection:
    """Tests for D8 flow direction computation."""

    def test_output_shape(self) -> None:
        """Output has same shape as input."""
        elevation = np.random.rand(50, 80).astype(np.float32)
        flow_dir = compute_d8_flow_direction(elevation)
        assert flow_dir.shape == elevation.shape

    def test_output_dtype(self) -> None:
        """Output is uint8."""
        elevation = np.random.rand(20, 20).astype(np.float32)
        flow_dir = compute_d8_flow_direction(elevation)
        assert flow_dir.dtype == np.uint8

    def test_flow_direction_downhill(self) -> None:
        """Flow direction points downhill."""
        # Create a simple slope: higher on left, lower on right
        elevation = np.zeros((5, 5), dtype=np.float32)
        for x in range(5):
            elevation[:, x] = 4 - x  # 4, 3, 2, 1, 0 from left to right

        flow_dir = compute_d8_flow_direction(elevation)

        # Interior cells should flow east (direction 2)
        assert flow_dir[2, 1] == 2  # E
        assert flow_dir[2, 2] == 2  # E

    def test_flat_area_nodata(self) -> None:
        """Flat areas have no flow direction."""
        elevation = np.ones((5, 5), dtype=np.float32)
        flow_dir = compute_d8_flow_direction(elevation)

        # Interior cells should have no downhill neighbor
        assert flow_dir[2, 2] == FLOW_NODATA

    def test_pit_nodata(self) -> None:
        """Pits (local minima) have no flow direction."""
        elevation = np.ones((5, 5), dtype=np.float32)
        elevation[2, 2] = 0.0  # Pit in center

        flow_dir = compute_d8_flow_direction(elevation)

        # Pit should have no outflow
        assert flow_dir[2, 2] == FLOW_NODATA

    def test_flow_direction_values_valid(self) -> None:
        """All flow direction values are 0-7 or NODATA."""
        elevation = np.random.rand(30, 30).astype(np.float32)
        flow_dir = compute_d8_flow_direction(elevation)

        valid_values = set(range(8)) | {FLOW_NODATA}
        unique_values = set(np.unique(flow_dir))
        assert unique_values.issubset(valid_values)

    def test_edge_cells_respect_bounds(self) -> None:
        """Edge cells don't point out of bounds."""
        # Slope toward edges
        elevation = np.zeros((10, 10), dtype=np.float32)
        for y in range(10):
            for x in range(10):
                elevation[y, x] = abs(y - 5) + abs(x - 5)

        flow_dir = compute_d8_flow_direction(elevation)

        # Check that following any flow direction stays in bounds
        for y in range(10):
            for x in range(10):
                d = flow_dir[y, x]
                if d != FLOW_NODATA:
                    ny = y + D8_DY[d]
                    nx = x + D8_DX[d]
                    assert 0 <= ny < 10, f"Out of bounds at ({y}, {x}) direction {d}"
                    assert 0 <= nx < 10, f"Out of bounds at ({y}, {x}) direction {d}"


class TestFlowAccumulation:
    """Tests for flow accumulation computation."""

    def test_output_shape(self) -> None:
        """Output has same shape as input."""
        flow_dir = np.full((30, 40), FLOW_NODATA, dtype=np.uint8)
        acc = compute_flow_accumulation(flow_dir)
        assert acc.shape == flow_dir.shape

    def test_output_dtype(self) -> None:
        """Output is uint32."""
        flow_dir = np.full((10, 10), FLOW_NODATA, dtype=np.uint8)
        acc = compute_flow_accumulation(flow_dir)
        assert acc.dtype == np.uint32

    def test_no_flow_all_ones(self) -> None:
        """With no flow, all cells have accumulation of 1."""
        flow_dir = np.full((10, 10), FLOW_NODATA, dtype=np.uint8)
        acc = compute_flow_accumulation(flow_dir)
        np.testing.assert_array_equal(acc, np.ones((10, 10), dtype=np.uint32))

    def test_linear_flow_accumulates(self) -> None:
        """Linear flow accumulates properly."""
        # Create a linear flow: all cells flow east (direction 2)
        flow_dir = np.full((3, 5), 2, dtype=np.uint8)  # E
        flow_dir[:, -1] = FLOW_NODATA  # Right edge has no outflow

        acc = compute_flow_accumulation(flow_dir)

        # Each row: accumulation should increase toward the right
        # Column 0: 1, Column 1: 2 (col 0 flows to it), etc.
        for row in range(3):
            for col in range(5):
                expected = col + 1
                assert acc[row, col] == expected, f"Row {row}, col {col}"

    def test_convergent_flow(self) -> None:
        """Multiple streams converging accumulate correctly."""
        # Simple 3x3 where all flow to center, center flows south
        flow_dir = np.array([
            [3, 4, 5],  # SE, S, SW
            [2, 4, 6],  # E, S, W
            [FLOW_NODATA, FLOW_NODATA, FLOW_NODATA],
        ], dtype=np.uint8)

        acc = compute_flow_accumulation(flow_dir)

        # Center cell (1,1) should have accumulation from all 8 neighbors + itself
        # But our grid is 3x3, so only 8 cells flow into row 2
        # Actually: top row flows to center, center flows south, left/right flow to center
        # Top-left (SE) -> center, top (S) -> center, top-right (SW) -> center
        # Left (E) -> center, Right (W) -> center
        # Center (S) -> (2,1)
        # So center should have: 1 (itself) + 5 (from top row + sides) = 6
        assert acc[1, 1] == 6

        # Bottom center receives from center (6) + itself (1) = 7
        assert acc[2, 1] == 7


class TestPriorityFloodFill:
    """Tests for pit filling algorithm."""

    def test_output_shape(self) -> None:
        """Output has same shape as input."""
        elevation = np.random.rand(40, 50).astype(np.float32)
        ocean_mask = np.zeros((40, 50), dtype=bool)
        ocean_mask[0, :] = True  # Ocean at top

        filled = priority_flood_fill(elevation, ocean_mask)
        assert filled.shape == elevation.shape

    def test_output_dtype(self) -> None:
        """Output is float32."""
        elevation = np.random.rand(20, 20).astype(np.float32)
        ocean_mask = np.zeros((20, 20), dtype=bool)
        ocean_mask[:, 0] = True

        filled = priority_flood_fill(elevation, ocean_mask)
        assert filled.dtype == np.float32

    def test_simple_pit_filled(self) -> None:
        """Simple pit is filled."""
        elevation = np.ones((5, 5), dtype=np.float32)
        elevation[2, 2] = 0.0  # Pit in center

        ocean_mask = np.zeros((5, 5), dtype=bool)
        ocean_mask[0, :] = True  # Ocean at top

        filled = priority_flood_fill(elevation, ocean_mask)

        # Pit should be raised to level of surrounding terrain
        assert filled[2, 2] >= elevation[1, 2]  # At least as high as neighbor

    def test_ocean_unchanged(self) -> None:
        """Ocean cells are unchanged."""
        elevation = np.ones((10, 10), dtype=np.float32) * 5.0
        ocean_mask = np.zeros((10, 10), dtype=bool)
        ocean_mask[:3, :] = True

        filled = priority_flood_fill(elevation, ocean_mask)

        # Ocean region should retain original elevation
        np.testing.assert_array_equal(filled[:3, :], elevation[:3, :])

    def test_no_pits_unchanged(self) -> None:
        """Terrain without pits is unchanged."""
        # Create smooth slope toward ocean
        elevation = np.zeros((10, 10), dtype=np.float32)
        for y in range(10):
            elevation[y, :] = y * 0.1  # Higher inland

        ocean_mask = np.zeros((10, 10), dtype=bool)
        ocean_mask[0, :] = True  # Ocean at y=0

        filled = priority_flood_fill(elevation, ocean_mask)

        # Land should be unchanged since it drains naturally
        np.testing.assert_array_almost_equal(filled[1:, :], elevation[1:, :])
