"""Conversion functions between internal types and proto messages."""

from . import world_pb2 as pb
from .state import Entity, Tile
from .types import Direction, Position


def direction_to_proto(direction: Direction) -> pb.Direction:
    """Convert internal Direction to proto Direction."""
    # Values match between internal IntEnum and proto enum
    return pb.Direction.Value(direction.name)


def direction_from_proto(proto_direction: pb.Direction) -> Direction | None:
    """Convert proto Direction to internal Direction.

    Returns None for DIRECTION_UNSPECIFIED.
    """
    if proto_direction == pb.DIRECTION_UNSPECIFIED:
        return None
    # Values match between proto enum and internal IntEnum
    return Direction(proto_direction)


def position_to_proto(position: Position) -> pb.Position:
    """Convert internal Position to proto Position."""
    return pb.Position(x=position.x, y=position.y)


def position_from_proto(proto_position: pb.Position) -> Position:
    """Convert proto Position to internal Position."""
    return Position(x=proto_position.x, y=proto_position.y)


def entity_to_proto(entity: Entity) -> pb.Entity:
    """Convert internal Entity to proto Entity."""
    return pb.Entity(
        entity_id=entity.entity_id,
        position=position_to_proto(entity.position),
        entity_type=entity.entity_type,
        tags=list(entity.tags),
        status_bits=entity.status_bits,
        # inventory deferred to Milestone 5
    )


def entity_from_proto(proto_entity: pb.Entity) -> Entity:
    """Convert proto Entity to internal Entity."""
    return Entity(
        entity_id=proto_entity.entity_id,
        position=position_from_proto(proto_entity.position),
        entity_type=proto_entity.entity_type,
        tags=tuple(proto_entity.tags),
        status_bits=proto_entity.status_bits,
    )


def tile_to_proto(tile: Tile) -> pb.Tile:
    """Convert internal Tile to proto Tile."""
    return pb.Tile(
        position=position_to_proto(tile.position),
        walkable=tile.walkable,
        opaque=tile.opaque,
        floor_type=tile.floor_type,
    )


def tile_from_proto(proto_tile: pb.Tile) -> Tile:
    """Convert proto Tile to internal Tile."""
    return Tile(
        position=position_from_proto(proto_tile.position),
        walkable=proto_tile.walkable,
        opaque=proto_tile.opaque,
        floor_type=proto_tile.floor_type,
    )
