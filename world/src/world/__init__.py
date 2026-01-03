"""World simulation core."""

from .exceptions import (
    EntityAlreadyExistsError,
    EntityNotFoundError,
    InvalidMoveError,
    PositionOccupiedError,
    TickDeadlineError,
    WorldError,
)
from .movement import MoveClaim, MoveResult, MovementResolver, process_movement_phase
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
    # Exceptions
    "WorldError",
    "InvalidMoveError",
    "EntityNotFoundError",
    "EntityAlreadyExistsError",
    "PositionOccupiedError",
    "TickDeadlineError",
]
