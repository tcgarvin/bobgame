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
from .combat import kill_entity, process_attack_phase
from .containers import (
    add_items_to_ground,
    process_give_phase,
    encode_contents,
    encode_notes,
    read_contents,
    read_notes,
)
from .conversations import process_conversation_phase
from .crafting import RECIPES, Recipe, process_craft_phase
from .items import ITEM_KINDS, attack_damage
from .lease import Lease, LeaseManager
from .movement import MoveClaim, MoveResult, MovementResolver, process_movement_phase
from .bootstrap import ServerSettings, run_server
from .server import WorldServer
from .state import Entity, Tile, World
from .stats import find_free_tile, process_respawns, process_rest_phase
from .tick import (
    TickConfig,
    TickContext,
    TickLoop,
    TickResult,
    process_tick,
    run_ticks,
)
from .types import (
    DIAGONAL_COMPONENTS,
    DIRECTION_DELTAS,
    AttackIntent,
    CollectIntent,
    ConverseIntent,
    CraftIntent,
    DepositIntent,
    Direction,
    DropIntent,
    EatIntent,
    EntityIntent,
    EquipIntent,
    ExtractIntent,
    GiveIntent,
    MoveIntent,
    PickupIntent,
    PlaceIntent,
    Position,
    RestIntent,
    SayIntent,
    WaitIntent,
    WithdrawIntent,
    WriteNoteIntent,
    chebyshev_distance,
)
from .wolves import WolfSimulator

__all__ = [
    # Types
    "Direction",
    "Position",
    "MoveIntent",
    "EntityIntent",
    "AttackIntent",
    "CollectIntent",
    "ConverseIntent",
    "CraftIntent",
    "DepositIntent",
    "DropIntent",
    "EatIntent",
    "EquipIntent",
    "ExtractIntent",
    "GiveIntent",
    "PickupIntent",
    "PlaceIntent",
    "SayIntent",
    "WaitIntent",
    "WithdrawIntent",
    "WriteNoteIntent",
    "chebyshev_distance",
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
    "process_tick",
    "run_ticks",
    # Mechanics
    "ITEM_KINDS",
    "RECIPES",
    "Recipe",
    "RestIntent",
    "WolfSimulator",
    "attack_damage",
    "kill_entity",
    "process_attack_phase",
    "process_conversation_phase",
    "process_craft_phase",
    "process_give_phase",
    "process_respawns",
    "process_rest_phase",
    "find_free_tile",
    "add_items_to_ground",
    "read_contents",
    "encode_contents",
    "read_notes",
    "encode_notes",
    # Lease
    "Lease",
    "LeaseManager",
    # Server
    "WorldServer",
    "ServerSettings",
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
