"""Terrain floor types, their numeric codes and their properties.

The single source of truth for the numeric floor codes. They are a wire
format: saved maps store them in a uint8 array and the viewer reads them from
`viewer/src/generated/rules.ts`, which `tools/generate_rules_ts.py` writes from
this table.
"""

from enum import Enum
from typing import Mapping


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
    def code(self) -> int:
        """The uint8 this floor type is stored as in a floor array."""
        return _CODES[self]

    @property
    def walkable(self) -> bool:
        """Whether entities can walk on this terrain type."""
        return self in _WALKABLE_TYPES

    @property
    def opaque(self) -> bool:
        """Whether this terrain blocks line of sight."""
        return self in _OPAQUE_TYPES


# Define sets for O(1) lookup
_WALKABLE_TYPES = frozenset(
    {
        FloorType.SHALLOW_WATER,
        FloorType.SAND,
        FloorType.GRASS,
        FloorType.DIRT,
        FloorType.STONE,
    }
)

_OPAQUE_TYPES = frozenset(
    {
        FloorType.MOUNTAIN,
    }
)


# Floor type -> the uint8 stored in a floor array. Never renumber: saved maps
# and the viewer both read these.
_CODES: Mapping["FloorType", int] = {
    FloorType.DEEP_WATER: 0,
    FloorType.SHALLOW_WATER: 1,
    FloorType.SAND: 2,
    FloorType.GRASS: 3,
    FloorType.DIRT: 4,
    FloorType.MOUNTAIN: 5,
    FloorType.STONE: 6,
}

FLOOR_TYPE_BY_CODE: Mapping[int, FloorType] = {
    code: floor_type for floor_type, code in _CODES.items()
}

# What an unknown code in a floor array reads as.
DEFAULT_FLOOR_TYPE = FloorType.STONE
