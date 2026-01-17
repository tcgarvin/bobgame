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


def decode_terrain_rle(data: bytes, shape: tuple[int, int]) -> NDArray[np.uint8]:
    """Decode RLE-compressed terrain data.

    Args:
        data: RLE-encoded bytes from encode_terrain_rle.
        shape: Expected output shape (height, width).

    Returns:
        2D uint8 array with decoded terrain.

    Raises:
        ValueError: If decoded length doesn't match expected shape.
    """
    expected_size = shape[0] * shape[1]
    result = np.zeros(expected_size, dtype=np.uint8)

    pos = 0
    i = 0
    while i < len(data) - 1:
        value = data[i]
        count = data[i + 1]
        if pos + count > expected_size:
            raise ValueError(
                f"RLE decode overflow: {pos + count} > {expected_size}"
            )
        result[pos : pos + count] = value
        pos += count
        i += 2

    if pos != expected_size:
        raise ValueError(
            f"RLE decode size mismatch: got {pos}, expected {expected_size}"
        )

    return result.reshape(shape)


def encode_terrain_base64(terrain: NDArray[np.uint8]) -> str:
    """Encode terrain as base64 string (RLE compressed).

    Args:
        terrain: 2D uint8 array.

    Returns:
        Base64-encoded string of RLE-compressed data.
    """
    rle_bytes = encode_terrain_rle(terrain)
    return base64.b64encode(rle_bytes).decode("ascii")


def decode_terrain_base64(data: str, shape: tuple[int, int]) -> NDArray[np.uint8]:
    """Decode base64-encoded RLE terrain data.

    Args:
        data: Base64-encoded string.
        shape: Expected output shape (height, width).

    Returns:
        2D uint8 array with decoded terrain.
    """
    rle_bytes = base64.b64decode(data)
    return decode_terrain_rle(rle_bytes, shape)


def encode_terrain_changes(
    changes: list[tuple[int, int, int]]
) -> list[dict[str, int]]:
    """Encode sparse terrain changes as JSON-serializable list.

    Args:
        changes: List of (local_x, local_y, floor_type) tuples.

    Returns:
        List of dicts with x, y, floor_type keys.
    """
    return [{"x": x, "y": y, "floor_type": ft} for x, y, ft in changes]


def decode_terrain_changes(
    data: list[dict[str, int]]
) -> list[tuple[int, int, int]]:
    """Decode sparse terrain changes from JSON.

    Args:
        data: List of dicts with x, y, floor_type keys.

    Returns:
        List of (local_x, local_y, floor_type) tuples.
    """
    return [(d["x"], d["y"], d["floor_type"]) for d in data]
