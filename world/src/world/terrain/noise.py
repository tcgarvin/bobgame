"""Noise generation functions for terrain generation.

Provides fBm (fractal Brownian motion), ridged multifractal,
and domain warping implementations.
"""

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage


def _gaussian_noise_2d(
    width: int,
    height: int,
    rng: np.random.Generator,
    wavelength: float,
) -> NDArray[np.float32]:
    """Generate smooth noise using Gaussian filter on random field.

    Uses FFT-based filtering for large sigmas for better performance.

    Args:
        width: Output width.
        height: Output height.
        rng: Random number generator.
        wavelength: Approximate wavelength of features in tiles.

    Returns:
        2D noise array in range roughly [-1, 1].
    """
    # Generate white noise
    white_noise = rng.standard_normal((height, width)).astype(np.float32)

    # sigma proportional to wavelength
    sigma = wavelength / 3.0

    # Use FFT-based filtering for large sigmas (faster for sigma > ~30)
    if sigma > 30:
        smoothed = _fft_gaussian_filter(white_noise, sigma)
    else:
        smoothed = ndimage.gaussian_filter(white_noise, sigma=sigma, mode="wrap")

    # Normalize to roughly [-1, 1]
    std = np.std(smoothed)
    if std > 0:
        smoothed /= (2.5 * std)

    return smoothed


def _fft_gaussian_filter(
    data: NDArray[np.float32],
    sigma: float,
) -> NDArray[np.float32]:
    """Apply Gaussian filter using FFT (faster for large sigma).

    Args:
        data: Input 2D array.
        sigma: Gaussian sigma.

    Returns:
        Filtered array.
    """
    from scipy.fft import fft2, ifft2, fftfreq

    height, width = data.shape

    # Create frequency grids
    fy = fftfreq(height)
    fx = fftfreq(width)
    fx_grid, fy_grid = np.meshgrid(fx, fy)

    # Gaussian in frequency domain
    # FFT of Gaussian with sigma is Gaussian with 1/(2*pi*sigma)
    freq_sigma = 1.0 / (2.0 * np.pi * sigma)
    gaussian_freq = np.exp(-0.5 * (fx_grid**2 + fy_grid**2) / (freq_sigma**2))

    # Apply in frequency domain
    data_fft = fft2(data)
    filtered_fft = data_fft * gaussian_freq
    result = np.real(ifft2(filtered_fft))

    return result.astype(np.float32)


def fbm_noise_vectorized(
    width: int,
    height: int,
    seed: int,
    base_wavelength: float,
    octaves: int = 6,
    lacunarity: float = 2.0,
    gain: float = 0.5,
) -> NDArray[np.float32]:
    """Generate fractal Brownian motion noise using Gaussian filters.

    Sums multiple octaves of noise at increasing frequencies
    and decreasing amplitudes for natural-looking variation.

    Uses fast Gaussian-filtered noise instead of per-pixel simplex
    for much better performance on large grids.

    Args:
        width: Output width in tiles.
        height: Output height in tiles.
        seed: Random seed for noise generation.
        base_wavelength: Wavelength of the base (lowest) frequency in tiles.
        octaves: Number of noise layers to sum.
        lacunarity: Frequency multiplier between octaves.
        gain: Amplitude multiplier between octaves.

    Returns:
        2D array of noise values, roughly in range [-1, 1].
    """
    rng = np.random.default_rng(seed)
    result = np.zeros((height, width), dtype=np.float32)

    wavelength = base_wavelength
    amplitude = 1.0
    max_amplitude = 0.0

    for i in range(octaves):
        # Generate octave with unique seed offset
        octave_rng = np.random.default_rng(seed + i * 1000)
        octave_noise = _gaussian_noise_2d(width, height, octave_rng, wavelength)
        result += amplitude * octave_noise
        max_amplitude += amplitude
        wavelength /= lacunarity
        amplitude *= gain

    # Normalize to roughly [-1, 1]
    result /= max_amplitude
    return result


def ridged_multifractal(
    width: int,
    height: int,
    seed: int,
    base_wavelength: float,
    octaves: int = 4,
    lacunarity: float = 2.0,
    gain: float = 0.5,
    offset: float = 1.0,
) -> NDArray[np.float32]:
    """Generate ridged multifractal noise.

    Creates sharp ridges by taking absolute value and inverting,
    useful for mountain ranges.

    Args:
        width: Output width in tiles.
        height: Output height in tiles.
        seed: Random seed for noise generation.
        base_wavelength: Wavelength of the base frequency in tiles.
        octaves: Number of noise layers.
        lacunarity: Frequency multiplier between octaves.
        gain: Amplitude multiplier between octaves.
        offset: Value subtracted from absolute noise (controls ridge sharpness).

    Returns:
        2D array of noise values, roughly in range [0, 1].
    """
    result = np.zeros((height, width), dtype=np.float32)

    wavelength = base_wavelength
    amplitude = 1.0
    weight = np.ones((height, width), dtype=np.float32)
    max_value = 0.0

    for i in range(octaves):
        # Generate octave with unique seed
        octave_rng = np.random.default_rng(seed + 500 + i * 1000)
        raw_noise = _gaussian_noise_2d(width, height, octave_rng, wavelength)

        # Convert to ridge: offset - |noise|, then square
        signal = offset - np.abs(raw_noise)
        signal = signal * signal
        signal *= weight

        result += signal * amplitude

        # Update weight for next octave
        weight = np.clip(signal * 2.0, 0.0, 1.0)

        max_value += amplitude
        wavelength /= lacunarity
        amplitude *= gain

    # Normalize to [0, 1]
    result /= max_value
    return result


def domain_warp(
    field: NDArray[np.float32],
    seed: int,
    warp_wavelength: float,
    warp_amplitude: float,
    warp_octaves: int = 3,
) -> NDArray[np.float32]:
    """Apply domain warping to a field for organic distortion.

    Offsets sample coordinates using noise, creating flowing
    organic distortions instead of regular patterns.

    Args:
        field: Input 2D field to warp.
        seed: Random seed for warp noise.
        warp_wavelength: Wavelength of warp noise.
        warp_amplitude: Maximum offset in tiles.
        warp_octaves: Octaves for warp noise.

    Returns:
        Warped 2D field.
    """
    from scipy.ndimage import map_coordinates

    height, width = field.shape

    # Generate warp offset fields
    warp_x = fbm_noise_vectorized(
        width, height, seed, warp_wavelength, octaves=warp_octaves
    )
    warp_y = fbm_noise_vectorized(
        width, height, seed + 1000, warp_wavelength, octaves=warp_octaves
    )

    # Scale to amplitude
    warp_x *= warp_amplitude
    warp_y *= warp_amplitude

    # Create coordinate arrays
    ys, xs = np.meshgrid(np.arange(height), np.arange(width), indexing="ij")

    # Warped coordinates
    warped_x = np.clip(xs + warp_x, 0, width - 1).astype(np.float64)
    warped_y = np.clip(ys + warp_y, 0, height - 1).astype(np.float64)

    # Use scipy's map_coordinates for efficient bilinear interpolation
    # coordinates are in (row, col) order for map_coordinates
    coords = np.array([warped_y, warped_x])
    result = map_coordinates(field, coords, order=1, mode="nearest")

    return result.astype(np.float32)


def smoothstep(edge0: float, edge1: float, x: NDArray[np.float32]) -> NDArray[np.float32]:
    """Smooth Hermite interpolation between 0 and 1.

    Args:
        edge0: Lower edge of transition.
        edge1: Upper edge of transition.
        x: Input values.

    Returns:
        Smoothly interpolated values in [0, 1].
    """
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)
