from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Direction(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    DIRECTION_UNSPECIFIED: _ClassVar[Direction]
    NORTH: _ClassVar[Direction]
    NORTHEAST: _ClassVar[Direction]
    EAST: _ClassVar[Direction]
    SOUTHEAST: _ClassVar[Direction]
    SOUTH: _ClassVar[Direction]
    SOUTHWEST: _ClassVar[Direction]
    WEST: _ClassVar[Direction]
    NORTHWEST: _ClassVar[Direction]
DIRECTION_UNSPECIFIED: Direction
NORTH: Direction
NORTHEAST: Direction
EAST: Direction
SOUTHEAST: Direction
SOUTH: Direction
SOUTHWEST: Direction
WEST: Direction
NORTHWEST: Direction

class Position(_message.Message):
    __slots__ = ("x", "y")
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    x: int
    y: int
    def __init__(self, x: _Optional[int] = ..., y: _Optional[int] = ...) -> None: ...

class Entity(_message.Message):
    __slots__ = ("entity_id", "position", "entity_type", "tags", "status_bits", "inventory")
    ENTITY_ID_FIELD_NUMBER: _ClassVar[int]
    POSITION_FIELD_NUMBER: _ClassVar[int]
    ENTITY_TYPE_FIELD_NUMBER: _ClassVar[int]
    TAGS_FIELD_NUMBER: _ClassVar[int]
    STATUS_BITS_FIELD_NUMBER: _ClassVar[int]
    INVENTORY_FIELD_NUMBER: _ClassVar[int]
    entity_id: str
    position: Position
    entity_type: str
    tags: _containers.RepeatedScalarFieldContainer[str]
    status_bits: int
    inventory: Inventory
    def __init__(self, entity_id: _Optional[str] = ..., position: _Optional[_Union[Position, _Mapping]] = ..., entity_type: _Optional[str] = ..., tags: _Optional[_Iterable[str]] = ..., status_bits: _Optional[int] = ..., inventory: _Optional[_Union[Inventory, _Mapping]] = ...) -> None: ...

class Inventory(_message.Message):
    __slots__ = ("items",)
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    items: _containers.RepeatedCompositeFieldContainer[InventoryItem]
    def __init__(self, items: _Optional[_Iterable[_Union[InventoryItem, _Mapping]]] = ...) -> None: ...

class InventoryItem(_message.Message):
    __slots__ = ("kind", "quantity")
    KIND_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    kind: str
    quantity: int
    def __init__(self, kind: _Optional[str] = ..., quantity: _Optional[int] = ...) -> None: ...

class Tile(_message.Message):
    __slots__ = ("position", "walkable", "opaque", "floor_type")
    POSITION_FIELD_NUMBER: _ClassVar[int]
    WALKABLE_FIELD_NUMBER: _ClassVar[int]
    OPAQUE_FIELD_NUMBER: _ClassVar[int]
    FLOOR_TYPE_FIELD_NUMBER: _ClassVar[int]
    position: Position
    walkable: bool
    opaque: bool
    floor_type: str
    def __init__(self, position: _Optional[_Union[Position, _Mapping]] = ..., walkable: bool = ..., opaque: bool = ..., floor_type: _Optional[str] = ...) -> None: ...

class WorldObject(_message.Message):
    __slots__ = ("object_id", "position", "object_type", "state")
    class StateEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    OBJECT_ID_FIELD_NUMBER: _ClassVar[int]
    POSITION_FIELD_NUMBER: _ClassVar[int]
    OBJECT_TYPE_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    object_id: str
    position: Position
    object_type: str
    state: _containers.ScalarMap[str, str]
    def __init__(self, object_id: _Optional[str] = ..., position: _Optional[_Union[Position, _Mapping]] = ..., object_type: _Optional[str] = ..., state: _Optional[_Mapping[str, str]] = ...) -> None: ...

class StreamTicksRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class TickEvent(_message.Message):
    __slots__ = ("tick_id", "tick_start_server_time_ms", "intent_deadline_server_time_ms", "tick_duration_ms", "world_version")
    TICK_ID_FIELD_NUMBER: _ClassVar[int]
    TICK_START_SERVER_TIME_MS_FIELD_NUMBER: _ClassVar[int]
    INTENT_DEADLINE_SERVER_TIME_MS_FIELD_NUMBER: _ClassVar[int]
    TICK_DURATION_MS_FIELD_NUMBER: _ClassVar[int]
    WORLD_VERSION_FIELD_NUMBER: _ClassVar[int]
    tick_id: int
    tick_start_server_time_ms: int
    intent_deadline_server_time_ms: int
    tick_duration_ms: int
    world_version: str
    def __init__(self, tick_id: _Optional[int] = ..., tick_start_server_time_ms: _Optional[int] = ..., intent_deadline_server_time_ms: _Optional[int] = ..., tick_duration_ms: _Optional[int] = ..., world_version: _Optional[str] = ...) -> None: ...

class ListControllableEntitiesRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class ControllableEntitiesResponse(_message.Message):
    __slots__ = ("entities",)
    ENTITIES_FIELD_NUMBER: _ClassVar[int]
    entities: _containers.RepeatedCompositeFieldContainer[ControllableEntity]
    def __init__(self, entities: _Optional[_Iterable[_Union[ControllableEntity, _Mapping]]] = ...) -> None: ...

class ControllableEntity(_message.Message):
    __slots__ = ("entity_id", "entity_type", "tags", "spawn_tick", "has_active_lease")
    ENTITY_ID_FIELD_NUMBER: _ClassVar[int]
    ENTITY_TYPE_FIELD_NUMBER: _ClassVar[int]
    TAGS_FIELD_NUMBER: _ClassVar[int]
    SPAWN_TICK_FIELD_NUMBER: _ClassVar[int]
    HAS_ACTIVE_LEASE_FIELD_NUMBER: _ClassVar[int]
    entity_id: str
    entity_type: str
    tags: _containers.RepeatedScalarFieldContainer[str]
    spawn_tick: int
    has_active_lease: bool
    def __init__(self, entity_id: _Optional[str] = ..., entity_type: _Optional[str] = ..., tags: _Optional[_Iterable[str]] = ..., spawn_tick: _Optional[int] = ..., has_active_lease: bool = ...) -> None: ...

class AcquireLeaseRequest(_message.Message):
    __slots__ = ("entity_id", "controller_id")
    ENTITY_ID_FIELD_NUMBER: _ClassVar[int]
    CONTROLLER_ID_FIELD_NUMBER: _ClassVar[int]
    entity_id: str
    controller_id: str
    def __init__(self, entity_id: _Optional[str] = ..., controller_id: _Optional[str] = ...) -> None: ...

class RenewLeaseRequest(_message.Message):
    __slots__ = ("lease_id",)
    LEASE_ID_FIELD_NUMBER: _ClassVar[int]
    lease_id: str
    def __init__(self, lease_id: _Optional[str] = ...) -> None: ...

class ReleaseLeaseRequest(_message.Message):
    __slots__ = ("lease_id",)
    LEASE_ID_FIELD_NUMBER: _ClassVar[int]
    lease_id: str
    def __init__(self, lease_id: _Optional[str] = ...) -> None: ...

class LeaseResponse(_message.Message):
    __slots__ = ("success", "lease_id", "expires_at_ms", "reason")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    LEASE_ID_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_AT_MS_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    success: bool
    lease_id: str
    expires_at_ms: int
    reason: str
    def __init__(self, success: bool = ..., lease_id: _Optional[str] = ..., expires_at_ms: _Optional[int] = ..., reason: _Optional[str] = ...) -> None: ...

class ReleaseLeaseResponse(_message.Message):
    __slots__ = ("success",)
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    success: bool
    def __init__(self, success: bool = ...) -> None: ...

class StreamObservationsRequest(_message.Message):
    __slots__ = ("lease_id", "entity_id")
    LEASE_ID_FIELD_NUMBER: _ClassVar[int]
    ENTITY_ID_FIELD_NUMBER: _ClassVar[int]
    lease_id: str
    entity_id: str
    def __init__(self, lease_id: _Optional[str] = ..., entity_id: _Optional[str] = ...) -> None: ...

class Observation(_message.Message):
    __slots__ = ("tick_id", "deadline_ms", "self", "visible_tiles", "visible_entities", "visible_objects", "events")
    TICK_ID_FIELD_NUMBER: _ClassVar[int]
    DEADLINE_MS_FIELD_NUMBER: _ClassVar[int]
    SELF_FIELD_NUMBER: _ClassVar[int]
    VISIBLE_TILES_FIELD_NUMBER: _ClassVar[int]
    VISIBLE_ENTITIES_FIELD_NUMBER: _ClassVar[int]
    VISIBLE_OBJECTS_FIELD_NUMBER: _ClassVar[int]
    EVENTS_FIELD_NUMBER: _ClassVar[int]
    tick_id: int
    deadline_ms: int
    self: Entity
    visible_tiles: _containers.RepeatedCompositeFieldContainer[Tile]
    visible_entities: _containers.RepeatedCompositeFieldContainer[Entity]
    visible_objects: _containers.RepeatedCompositeFieldContainer[WorldObject]
    events: _containers.RepeatedCompositeFieldContainer[ObservationEvent]
    def __init__(self_, tick_id: _Optional[int] = ..., deadline_ms: _Optional[int] = ..., self: _Optional[_Union[Entity, _Mapping]] = ..., visible_tiles: _Optional[_Iterable[_Union[Tile, _Mapping]]] = ..., visible_entities: _Optional[_Iterable[_Union[Entity, _Mapping]]] = ..., visible_objects: _Optional[_Iterable[_Union[WorldObject, _Mapping]]] = ..., events: _Optional[_Iterable[_Union[ObservationEvent, _Mapping]]] = ...) -> None: ...

class ObservationEvent(_message.Message):
    __slots__ = ("entity_entered", "entity_left", "entity_moved", "entity_acted", "utterance", "object_changed")
    ENTITY_ENTERED_FIELD_NUMBER: _ClassVar[int]
    ENTITY_LEFT_FIELD_NUMBER: _ClassVar[int]
    ENTITY_MOVED_FIELD_NUMBER: _ClassVar[int]
    ENTITY_ACTED_FIELD_NUMBER: _ClassVar[int]
    UTTERANCE_FIELD_NUMBER: _ClassVar[int]
    OBJECT_CHANGED_FIELD_NUMBER: _ClassVar[int]
    entity_entered: EntityEnteredView
    entity_left: EntityLeftView
    entity_moved: EntityMoved
    entity_acted: EntityActed
    utterance: Utterance
    object_changed: ObjectChanged
    def __init__(self, entity_entered: _Optional[_Union[EntityEnteredView, _Mapping]] = ..., entity_left: _Optional[_Union[EntityLeftView, _Mapping]] = ..., entity_moved: _Optional[_Union[EntityMoved, _Mapping]] = ..., entity_acted: _Optional[_Union[EntityActed, _Mapping]] = ..., utterance: _Optional[_Union[Utterance, _Mapping]] = ..., object_changed: _Optional[_Union[ObjectChanged, _Mapping]] = ...) -> None: ...

class EntityEnteredView(_message.Message):
    __slots__ = ("entity",)
    ENTITY_FIELD_NUMBER: _ClassVar[int]
    entity: Entity
    def __init__(self, entity: _Optional[_Union[Entity, _Mapping]] = ...) -> None: ...

class EntityLeftView(_message.Message):
    __slots__ = ("entity_id", "last_known_position")
    ENTITY_ID_FIELD_NUMBER: _ClassVar[int]
    LAST_KNOWN_POSITION_FIELD_NUMBER: _ClassVar[int]
    entity_id: str
    last_known_position: Position
    def __init__(self, entity_id: _Optional[str] = ..., last_known_position: _Optional[_Union[Position, _Mapping]] = ...) -> None: ...

class EntityMoved(_message.Message):
    __slots__ = ("entity_id", "to")
    ENTITY_ID_FIELD_NUMBER: _ClassVar[int]
    FROM_FIELD_NUMBER: _ClassVar[int]
    TO_FIELD_NUMBER: _ClassVar[int]
    entity_id: str
    to: Position
    def __init__(self, entity_id: _Optional[str] = ..., to: _Optional[_Union[Position, _Mapping]] = ..., **kwargs) -> None: ...

class EntityActed(_message.Message):
    __slots__ = ("entity_id", "action_type", "success", "details")
    ENTITY_ID_FIELD_NUMBER: _ClassVar[int]
    ACTION_TYPE_FIELD_NUMBER: _ClassVar[int]
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    DETAILS_FIELD_NUMBER: _ClassVar[int]
    entity_id: str
    action_type: str
    success: bool
    details: str
    def __init__(self, entity_id: _Optional[str] = ..., action_type: _Optional[str] = ..., success: bool = ..., details: _Optional[str] = ...) -> None: ...

class Utterance(_message.Message):
    __slots__ = ("speaker_id", "channel", "text", "position")
    SPEAKER_ID_FIELD_NUMBER: _ClassVar[int]
    CHANNEL_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    POSITION_FIELD_NUMBER: _ClassVar[int]
    speaker_id: str
    channel: str
    text: str
    position: Position
    def __init__(self, speaker_id: _Optional[str] = ..., channel: _Optional[str] = ..., text: _Optional[str] = ..., position: _Optional[_Union[Position, _Mapping]] = ...) -> None: ...

class ObjectChanged(_message.Message):
    __slots__ = ("object_id", "field", "old_value", "new_value")
    OBJECT_ID_FIELD_NUMBER: _ClassVar[int]
    FIELD_FIELD_NUMBER: _ClassVar[int]
    OLD_VALUE_FIELD_NUMBER: _ClassVar[int]
    NEW_VALUE_FIELD_NUMBER: _ClassVar[int]
    object_id: str
    field: str
    old_value: str
    new_value: str
    def __init__(self, object_id: _Optional[str] = ..., field: _Optional[str] = ..., old_value: _Optional[str] = ..., new_value: _Optional[str] = ...) -> None: ...

class SubmitIntentRequest(_message.Message):
    __slots__ = ("lease_id", "entity_id", "tick_id", "intent")
    LEASE_ID_FIELD_NUMBER: _ClassVar[int]
    ENTITY_ID_FIELD_NUMBER: _ClassVar[int]
    TICK_ID_FIELD_NUMBER: _ClassVar[int]
    INTENT_FIELD_NUMBER: _ClassVar[int]
    lease_id: str
    entity_id: str
    tick_id: int
    intent: Intent
    def __init__(self, lease_id: _Optional[str] = ..., entity_id: _Optional[str] = ..., tick_id: _Optional[int] = ..., intent: _Optional[_Union[Intent, _Mapping]] = ...) -> None: ...

class Intent(_message.Message):
    __slots__ = ("move", "pickup", "use", "say", "wait", "collect", "eat")
    MOVE_FIELD_NUMBER: _ClassVar[int]
    PICKUP_FIELD_NUMBER: _ClassVar[int]
    USE_FIELD_NUMBER: _ClassVar[int]
    SAY_FIELD_NUMBER: _ClassVar[int]
    WAIT_FIELD_NUMBER: _ClassVar[int]
    COLLECT_FIELD_NUMBER: _ClassVar[int]
    EAT_FIELD_NUMBER: _ClassVar[int]
    move: MoveIntent
    pickup: PickupIntent
    use: UseIntent
    say: SayIntent
    wait: WaitIntent
    collect: CollectIntent
    eat: EatIntent
    def __init__(self, move: _Optional[_Union[MoveIntent, _Mapping]] = ..., pickup: _Optional[_Union[PickupIntent, _Mapping]] = ..., use: _Optional[_Union[UseIntent, _Mapping]] = ..., say: _Optional[_Union[SayIntent, _Mapping]] = ..., wait: _Optional[_Union[WaitIntent, _Mapping]] = ..., collect: _Optional[_Union[CollectIntent, _Mapping]] = ..., eat: _Optional[_Union[EatIntent, _Mapping]] = ...) -> None: ...

class MoveIntent(_message.Message):
    __slots__ = ("direction",)
    DIRECTION_FIELD_NUMBER: _ClassVar[int]
    direction: Direction
    def __init__(self, direction: _Optional[_Union[Direction, str]] = ...) -> None: ...

class PickupIntent(_message.Message):
    __slots__ = ("kind", "amount")
    KIND_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_FIELD_NUMBER: _ClassVar[int]
    kind: str
    amount: int
    def __init__(self, kind: _Optional[str] = ..., amount: _Optional[int] = ...) -> None: ...

class UseIntent(_message.Message):
    __slots__ = ("kind", "amount")
    KIND_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_FIELD_NUMBER: _ClassVar[int]
    kind: str
    amount: int
    def __init__(self, kind: _Optional[str] = ..., amount: _Optional[int] = ...) -> None: ...

class SayIntent(_message.Message):
    __slots__ = ("text", "channel")
    TEXT_FIELD_NUMBER: _ClassVar[int]
    CHANNEL_FIELD_NUMBER: _ClassVar[int]
    text: str
    channel: str
    def __init__(self, text: _Optional[str] = ..., channel: _Optional[str] = ...) -> None: ...

class WaitIntent(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class CollectIntent(_message.Message):
    __slots__ = ("object_id", "item_type", "amount")
    OBJECT_ID_FIELD_NUMBER: _ClassVar[int]
    ITEM_TYPE_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_FIELD_NUMBER: _ClassVar[int]
    object_id: str
    item_type: str
    amount: int
    def __init__(self, object_id: _Optional[str] = ..., item_type: _Optional[str] = ..., amount: _Optional[int] = ...) -> None: ...

class EatIntent(_message.Message):
    __slots__ = ("item_type", "amount")
    ITEM_TYPE_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_FIELD_NUMBER: _ClassVar[int]
    item_type: str
    amount: int
    def __init__(self, item_type: _Optional[str] = ..., amount: _Optional[int] = ...) -> None: ...

class SubmitIntentResponse(_message.Message):
    __slots__ = ("accepted", "reason")
    ACCEPTED_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    accepted: bool
    reason: str
    def __init__(self, accepted: bool = ..., reason: _Optional[str] = ...) -> None: ...

class ViewerFilter(_message.Message):
    __slots__ = ("entity_ids", "region_min", "region_max")
    ENTITY_IDS_FIELD_NUMBER: _ClassVar[int]
    REGION_MIN_FIELD_NUMBER: _ClassVar[int]
    REGION_MAX_FIELD_NUMBER: _ClassVar[int]
    entity_ids: _containers.RepeatedScalarFieldContainer[str]
    region_min: Position
    region_max: Position
    def __init__(self, entity_ids: _Optional[_Iterable[str]] = ..., region_min: _Optional[_Union[Position, _Mapping]] = ..., region_max: _Optional[_Union[Position, _Mapping]] = ...) -> None: ...

class ViewerEvent(_message.Message):
    __slots__ = ("tick_id", "tick_completed", "entity_spawned", "entity_despawned", "entity_moved", "entity_acted", "utterance", "object_changed")
    TICK_ID_FIELD_NUMBER: _ClassVar[int]
    TICK_COMPLETED_FIELD_NUMBER: _ClassVar[int]
    ENTITY_SPAWNED_FIELD_NUMBER: _ClassVar[int]
    ENTITY_DESPAWNED_FIELD_NUMBER: _ClassVar[int]
    ENTITY_MOVED_FIELD_NUMBER: _ClassVar[int]
    ENTITY_ACTED_FIELD_NUMBER: _ClassVar[int]
    UTTERANCE_FIELD_NUMBER: _ClassVar[int]
    OBJECT_CHANGED_FIELD_NUMBER: _ClassVar[int]
    tick_id: int
    tick_completed: TickCompleted
    entity_spawned: EntitySpawned
    entity_despawned: EntityDespawned
    entity_moved: EntityMoved
    entity_acted: EntityActed
    utterance: Utterance
    object_changed: ObjectChanged
    def __init__(self, tick_id: _Optional[int] = ..., tick_completed: _Optional[_Union[TickCompleted, _Mapping]] = ..., entity_spawned: _Optional[_Union[EntitySpawned, _Mapping]] = ..., entity_despawned: _Optional[_Union[EntityDespawned, _Mapping]] = ..., entity_moved: _Optional[_Union[EntityMoved, _Mapping]] = ..., entity_acted: _Optional[_Union[EntityActed, _Mapping]] = ..., utterance: _Optional[_Union[Utterance, _Mapping]] = ..., object_changed: _Optional[_Union[ObjectChanged, _Mapping]] = ...) -> None: ...

class TickCompleted(_message.Message):
    __slots__ = ("tick_id", "entities_moved", "actions_processed")
    TICK_ID_FIELD_NUMBER: _ClassVar[int]
    ENTITIES_MOVED_FIELD_NUMBER: _ClassVar[int]
    ACTIONS_PROCESSED_FIELD_NUMBER: _ClassVar[int]
    tick_id: int
    entities_moved: int
    actions_processed: int
    def __init__(self, tick_id: _Optional[int] = ..., entities_moved: _Optional[int] = ..., actions_processed: _Optional[int] = ...) -> None: ...

class EntitySpawned(_message.Message):
    __slots__ = ("entity",)
    ENTITY_FIELD_NUMBER: _ClassVar[int]
    entity: Entity
    def __init__(self, entity: _Optional[_Union[Entity, _Mapping]] = ...) -> None: ...

class EntityDespawned(_message.Message):
    __slots__ = ("entity_id", "reason")
    ENTITY_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    entity_id: str
    reason: str
    def __init__(self, entity_id: _Optional[str] = ..., reason: _Optional[str] = ...) -> None: ...

class WorldSnapshot(_message.Message):
    __slots__ = ("tick_id", "entities", "objects")
    TICK_ID_FIELD_NUMBER: _ClassVar[int]
    ENTITIES_FIELD_NUMBER: _ClassVar[int]
    OBJECTS_FIELD_NUMBER: _ClassVar[int]
    tick_id: int
    entities: _containers.RepeatedCompositeFieldContainer[Entity]
    objects: _containers.RepeatedCompositeFieldContainer[WorldObject]
    def __init__(self, tick_id: _Optional[int] = ..., entities: _Optional[_Iterable[_Union[Entity, _Mapping]]] = ..., objects: _Optional[_Iterable[_Union[WorldObject, _Mapping]]] = ...) -> None: ...
