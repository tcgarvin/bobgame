"""World simulation core."""

from .conversion import (
    direction_from_proto,
    direction_to_proto,
    entity_from_proto,
    entity_to_proto,
    position_from_proto,
    position_to_proto,
    tile_from_proto,
    tile_to_proto,
)
from .exceptions import (
    EntityAlreadyExistsError,
    EntityNotFoundError,
    InvalidMoveError,
    PositionOccupiedError,
    TickDeadlineError,
    WorldError,
)
from .lease import Lease, LeaseManager
from .movement import MoveClaim, MoveResult, MovementResolver, process_movement_phase
from .server import WorldServer, run_server
from .state import Entity, Tile, World
from .tick import TickConfig, TickContext, TickLoop, TickResult, run_ticks
from .types import (
    DIAGONAL_COMPONENTS,
    DIRECTION_DELTAS,
    Direction,
    MoveIntent,
    Position,
)

__all__ = [
    # Types
    "Direction",
    "Position",
    "MoveIntent",
    "DIRECTION_DELTAS",
    "DIAGONAL_COMPONENTS",
    # State
    "World",
    "Entity",
    "Tile",
    # Movement
    "MoveClaim",
    "MoveResult",
    "MovementResolver",
    "process_movement_phase",
    # Tick
    "TickConfig",
    "TickContext",
    "TickResult",
    "TickLoop",
    "run_ticks",
    # Lease
    "Lease",
    "LeaseManager",
    # Server
    "WorldServer",
    "run_server",
    # Conversion
    "direction_to_proto",
    "direction_from_proto",
    "position_to_proto",
    "position_from_proto",
    "entity_to_proto",
    "entity_from_proto",
    "tile_to_proto",
    "tile_from_proto",
    # Exceptions
    "WorldError",
    "InvalidMoveError",
    "EntityNotFoundError",
    "EntityAlreadyExistsError",
    "PositionOccupiedError",
    "TickDeadlineError",
]
