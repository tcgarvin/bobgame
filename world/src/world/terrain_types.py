"""Terrain floor types: the world's view of `bobgame_rules.terrain`.

The table itself lives in `rules/` so the viewer generator and the agents can
read it without importing the world. These re-exports keep the world's own
`world.terrain_types` import path working.
"""

from bobgame_rules.terrain import (
    DEFAULT_FLOOR_TYPE,
    FLOOR_TYPE_BY_CODE,
    FloorType,
)

__all__ = ["DEFAULT_FLOOR_TYPE", "FLOOR_TYPE_BY_CODE", "FloorType"]
