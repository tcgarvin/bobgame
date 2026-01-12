"""Tests for noise generation functions."""

import numpy as np
import pytest

from world.terrain.noise import (
    fbm_noise_vectorized,
    ridged_multifractal,
    domain_warp,
    smoothstep,
)


class TestFbmNoise:
    """Tests for fBm noise generation."""

    def test_output_shape(self) -> None:
        """Output has correct dimensions."""
        result = fbm_noise_vectorized(100, 50, seed=42, base_wavelength=50)
        assert result.shape == (50, 100)

    def test_deterministic_with_same_seed(self) -> None:
        """Same seed produces identical output."""
        result1 = fbm_noise_vectorized(64, 64, seed=123, base_wavelength=30)
        result2 = fbm_noise_vectorized(64, 64, seed=123, base_wavelength=30)
        np.testing.assert_array_equal(result1, result2)

    def test_different_seed_different_output(self) -> None:
        """Different seeds produce different output."""
        result1 = fbm_noise_vectorized(64, 64, seed=123, base_wavelength=30)
        result2 = fbm_noise_vectorized(64, 64, seed=456, base_wavelength=30)
        assert not np.allclose(result1, result2)

    def test_output_range_reasonable(self) -> None:
        """Output values are in a reasonable range."""
        result = fbm_noise_vectorized(100, 100, seed=42, base_wavelength=50)
        # Should be roughly in [-1, 1] range
        assert result.min() >= -2.0
        assert result.max() <= 2.0

    def test_output_dtype(self) -> None:
        """Output is float32."""
        result = fbm_noise_vectorized(32, 32, seed=42, base_wavelength=20)
        assert result.dtype == np.float32

    def test_more_octaves_more_detail(self) -> None:
        """More octaves adds higher frequency detail."""
        result_low = fbm_noise_vectorized(64, 64, seed=42, base_wavelength=30, octaves=2)
        result_high = fbm_noise_vectorized(64, 64, seed=42, base_wavelength=30, octaves=6)

        # High frequency content measured by gradient magnitude
        grad_low = np.abs(np.diff(result_low, axis=0)).mean()
        grad_high = np.abs(np.diff(result_high, axis=0)).mean()

        # More octaves should have more high-frequency variation
        assert grad_high > grad_low


class TestRidgedMultifractal:
    """Tests for ridged multifractal noise."""

    def test_output_shape(self) -> None:
        """Output has correct dimensions."""
        result = ridged_multifractal(80, 60, seed=42, base_wavelength=40)
        assert result.shape == (60, 80)

    def test_deterministic_with_same_seed(self) -> None:
        """Same seed produces identical output."""
        result1 = ridged_multifractal(64, 64, seed=999, base_wavelength=30)
        result2 = ridged_multifractal(64, 64, seed=999, base_wavelength=30)
        np.testing.assert_array_equal(result1, result2)

    def test_output_range_normalized(self) -> None:
        """Output is normalized to [0, 1] range."""
        result = ridged_multifractal(100, 100, seed=42, base_wavelength=50)
        assert result.min() >= 0.0
        assert result.max() <= 1.0

    def test_output_dtype(self) -> None:
        """Output is float32."""
        result = ridged_multifractal(32, 32, seed=42, base_wavelength=20)
        assert result.dtype == np.float32


class TestDomainWarp:
    """Tests for domain warping."""

    def test_output_shape_preserved(self) -> None:
        """Output has same shape as input."""
        field = np.random.rand(50, 80).astype(np.float32)
        result = domain_warp(field, seed=42, warp_wavelength=20, warp_amplitude=5)
        assert result.shape == field.shape

    def test_deterministic(self) -> None:
        """Same inputs produce same output."""
        field = np.random.rand(32, 32).astype(np.float32)
        result1 = domain_warp(field, seed=42, warp_wavelength=20, warp_amplitude=5)
        result2 = domain_warp(field, seed=42, warp_wavelength=20, warp_amplitude=5)
        np.testing.assert_array_equal(result1, result2)

    def test_zero_amplitude_identity(self) -> None:
        """Zero warp amplitude returns input unchanged."""
        field = np.random.rand(32, 32).astype(np.float32)
        result = domain_warp(field, seed=42, warp_wavelength=20, warp_amplitude=0)
        np.testing.assert_array_almost_equal(result, field, decimal=5)

    def test_output_dtype(self) -> None:
        """Output is float32."""
        field = np.random.rand(32, 32).astype(np.float32)
        result = domain_warp(field, seed=42, warp_wavelength=20, warp_amplitude=5)
        assert result.dtype == np.float32


class TestSmoothstep:
    """Tests for smoothstep function."""

    def test_below_edge0_returns_zero(self) -> None:
        """Values below edge0 return 0."""
        x = np.array([-1.0, 0.0, 0.1], dtype=np.float32)
        result = smoothstep(0.2, 0.8, x)
        np.testing.assert_array_equal(result[:2], [0.0, 0.0])

    def test_above_edge1_returns_one(self) -> None:
        """Values above edge1 return 1."""
        x = np.array([0.9, 1.0, 1.5], dtype=np.float32)
        result = smoothstep(0.2, 0.8, x)
        np.testing.assert_array_equal(result, [1.0, 1.0, 1.0])

    def test_midpoint_returns_half(self) -> None:
        """Midpoint between edges returns 0.5."""
        x = np.array([0.5], dtype=np.float32)
        result = smoothstep(0.0, 1.0, x)
        assert result[0] == 0.5

    def test_output_range(self) -> None:
        """Output is always in [0, 1]."""
        x = np.linspace(-1, 2, 100, dtype=np.float32)
        result = smoothstep(0.2, 0.8, x)
        assert result.min() >= 0.0
        assert result.max() <= 1.0
