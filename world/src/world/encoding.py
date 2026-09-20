"""Terrain encoding utilities for efficient chunk transmission."""

import base64
from io import BytesIO

import numpy as np
from numpy.typing import NDArray


def encode_terrain_rle(terrain: NDArray[np.uint8]) -> bytes:
    """Run-length encode a 2D terrain array.

    Flattens the array row-major and encodes as (value, count) pairs.
    Count is stored as 1 byte (max 255), split into multiple entries if needed.

    Args:
        terrain: 2D uint8 array (typically 32x32).

    Returns:
        Compressed bytes: [value, count, value, count, ...]
    """
    flat = terrain.flatten()
    if len(flat) == 0:
        return b""

    result = BytesIO()
    current_value = flat[0]
    count = 1

    for value in flat[1:]:
        if value == current_value and count < 255:
            count += 1
        else:
            result.write(bytes([current_value, count]))
            current_value = value
            count = 1

    # Write final run
    result.write(bytes([current_value, count]))
    return result.getvalue()


def encode_terrain_base64(terrain: NDArray[np.uint8]) -> str:
    """Encode terrain as base64 string (RLE compressed).

    Args:
        terrain: 2D uint8 array.

    Returns:
        Base64-encoded string of RLE-compressed data.
    """
    rle_bytes = encode_terrain_rle(terrain)
    return base64.b64encode(rle_bytes).decode("ascii")
