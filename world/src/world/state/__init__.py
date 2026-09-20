"""World state: the frozen models (`models.py`) and `World` (`world.py`).

Everything is re-exported here, so `from world.state import Entity, World`
keeps working wherever it is written.
"""

from .models import (
    DEFAULT_DAY_LENGTH_TICKS,
    DEFAULT_ENTITY_TYPE,
    NIGHT_START_DENOMINATOR,
    NIGHT_START_NUMERATOR,
    STATUS_BIT_DEAD,
    WOLF_ENTITY_TYPE,
    Entity,
    Inventory,
    Tile,
    WorldClock,
    WorldObject,
    night_start_tick,
)
from .world import World

__all__ = [
    "DEFAULT_DAY_LENGTH_TICKS",
    "DEFAULT_ENTITY_TYPE",
    "NIGHT_START_DENOMINATOR",
    "NIGHT_START_NUMERATOR",
    "STATUS_BIT_DEAD",
    "WOLF_ENTITY_TYPE",
    "Entity",
    "Inventory",
    "Tile",
    "World",
    "WorldClock",
    "WorldObject",
    "night_start_tick",
]
