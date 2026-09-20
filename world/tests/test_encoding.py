"""Tests for terrain encoding utilities.

Only the encoders are shipped: the viewer decodes the base64/RLE payload in
TypeScript, so the Python decoders were dead code. The roundtrip tests below
decode with a small local helper that mirrors the viewer's reader.
"""

import base64

import numpy as np
from numpy.typing import NDArray

from world.encoding import encode_terrain_base64, encode_terrain_rle


def decode_rle(data: bytes, shape: tuple[int, int]) -> NDArray[np.uint8]:
    """Decode `encode_terrain_rle` output, as the viewer's reader does."""
    expected_size = shape[0] * shape[1]
    result = np.zeros(expected_size, dtype=np.uint8)
    pos = 0
    for index in range(0, len(data) - 1, 2):
        value, count = data[index], data[index + 1]
        result[pos : pos + count] = value
        pos += count
    assert pos == expected_size, f"decoded {pos}, expected {expected_size}"
    return result.reshape(shape)


class TestRLEEncoding:
    """Tests for RLE terrain encoding."""

    def test_encode_uniform_terrain(self) -> None:
        """Uniform terrain compresses well."""
        terrain = np.full((32, 32), 3, dtype=np.uint8)
        encoded = encode_terrain_rle(terrain)

        # 1024 tiles of value 3
        # With max count 255, need 5 entries: 255+255+255+255+4 = 1024
        # Each entry is 2 bytes, so 10 bytes total
        assert len(encoded) == 10
        assert encoded[0] == 3  # value
        assert encoded[1] == 255  # count

    def test_encode_alternating_terrain(self) -> None:
        """Alternating values don't compress."""
        terrain = np.zeros((4, 4), dtype=np.uint8)
        terrain[0::2, 0::2] = 1
        terrain[1::2, 1::2] = 1

        decoded = decode_rle(encode_terrain_rle(terrain), (4, 4))

        np.testing.assert_array_equal(terrain, decoded)

    def test_roundtrip_random_terrain(self) -> None:
        """Random terrain survives roundtrip."""
        np.random.seed(42)
        terrain = np.random.randint(0, 7, size=(32, 32), dtype=np.uint8)

        decoded = decode_rle(encode_terrain_rle(terrain), (32, 32))

        np.testing.assert_array_equal(terrain, decoded)

    def test_roundtrip_natural_terrain(self) -> None:
        """Natural terrain (many same values) compresses and decompresses."""
        # Simulate typical terrain: mostly grass with some water
        terrain = np.full((32, 32), 3, dtype=np.uint8)  # grass
        terrain[0:5, :] = 0  # water at top
        terrain[:, 0:3] = 2  # sand on left

        decoded = decode_rle(encode_terrain_rle(terrain), (32, 32))

        np.testing.assert_array_equal(terrain, decoded)

    def test_empty_terrain(self) -> None:
        """Empty terrain encodes to empty bytes."""
        terrain = np.zeros((0,), dtype=np.uint8).reshape(0, 0)
        encoded = encode_terrain_rle(terrain)
        assert encoded == b""

    def test_single_value(self) -> None:
        """Single value terrain."""
        terrain = np.array([[5]], dtype=np.uint8)
        decoded = decode_rle(encode_terrain_rle(terrain), (1, 1))

        assert decoded[0, 0] == 5

    def test_compression_ratio_uniform(self) -> None:
        """Uniform terrain has good compression ratio."""
        terrain = np.full((32, 32), 3, dtype=np.uint8)
        encoded = encode_terrain_rle(terrain)

        raw_size = 32 * 32  # 1024 bytes
        compressed_size = len(encoded)

        ratio = raw_size / compressed_size
        assert ratio > 100  # Should be ~102x compression

    def test_compression_ratio_natural(self) -> None:
        """Natural terrain has reasonable compression."""
        # Create terrain with large regions
        terrain = np.full((32, 32), 3, dtype=np.uint8)
        terrain[0:16, :] = 0  # Top half water
        terrain[16:24, :] = 2  # Middle band sand

        encoded = encode_terrain_rle(terrain)
        raw_size = 32 * 32

        # Should compress reasonably (at least 10x)
        assert len(encoded) < raw_size / 10


class TestBase64Encoding:
    """Tests for base64 terrain encoding."""

    def test_base64_roundtrip(self) -> None:
        """Terrain survives base64 encoding roundtrip."""
        terrain = np.full((32, 32), 3, dtype=np.uint8)
        terrain[10:20, 10:20] = 5  # mountain square

        encoded = encode_terrain_base64(terrain)
        assert isinstance(encoded, str)

        decoded = decode_rle(base64.b64decode(encoded), (32, 32))
        np.testing.assert_array_equal(terrain, decoded)

    def test_base64_is_ascii(self) -> None:
        """Base64 output is ASCII."""
        terrain = np.full((32, 32), 3, dtype=np.uint8)
        encoded = encode_terrain_base64(terrain)

        # Should only contain base64 characters
        assert encoded.isascii()
        assert all(
            c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
            for c in encoded
        )
