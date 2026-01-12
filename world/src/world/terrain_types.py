"""Terrain floor types and their properties."""

from enum import Enum


class FloorType(str, Enum):
    """Terrain floor types with walkability and opacity properties."""

    DEEP_WATER = "deep_water"
    SHALLOW_WATER = "shallow_water"
    SAND = "sand"
    GRASS = "grass"
    DIRT = "dirt"
    MOUNTAIN = "mountain"
    STONE = "stone"

    @property
    def walkable(self) -> bool:
        """Whether entities can walk on this terrain type."""
        return self in _WALKABLE_TYPES

    @property
    def opaque(self) -> bool:
        """Whether this terrain blocks line of sight."""
        return self in _OPAQUE_TYPES


# Define sets for O(1) lookup
_WALKABLE_TYPES = frozenset({
    FloorType.SHALLOW_WATER,
    FloorType.SAND,
    FloorType.GRASS,
    FloorType.DIRT,
    FloorType.STONE,
})

_OPAQUE_TYPES = frozenset({
    FloorType.MOUNTAIN,
})
