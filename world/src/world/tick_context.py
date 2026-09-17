"""The per-tick intent inbox shared by the gRPC services and the wolf sim.

Lives in its own module so `wolves.py` can submit intents without importing
`tick.py` (which imports the wolf simulator). `world.tick` re-exports
`TickContext`, which remains the canonical import path.
"""

import time
from dataclasses import dataclass, field
from typing import Mapping, TypeVar

import structlog

from .state import World
from .types import (
    AttackIntent,
    CollectIntent,
    CraftIntent,
    DepositIntent,
    Direction,
    DropIntent,
    EatIntent,
    EntityIntent,
    EquipIntent,
    ExtractIntent,
    MoveIntent,
    PickupIntent,
    PlaceIntent,
    RestIntent,
    SayIntent,
    WaitIntent,
    WithdrawIntent,
    WriteNoteIntent,
)

logger = structlog.get_logger()

T = TypeVar("T", bound=EntityIntent)

# Intent model -> the action_type string used in results and logs.
INTENT_ACTION_TYPES: Mapping[type[EntityIntent], str] = {
    MoveIntent: "move",
    CollectIntent: "collect",
    EatIntent: "eat",
    AttackIntent: "attack",
    ExtractIntent: "extract",
    PickupIntent: "pickup",
    WithdrawIntent: "withdraw",
    DropIntent: "drop",
    DepositIntent: "deposit",
    CraftIntent: "craft",
    EquipIntent: "equip",
    PlaceIntent: "place",
    WriteNoteIntent: "write_note",
    RestIntent: "rest",
    SayIntent: "say",
    WaitIntent: "wait",
}

# Rejection reasons returned by submit_intent().
REASON_ACCEPTED = ""
REASON_LATE = "late_tick"
REASON_DUPLICATE = "duplicate"
REASON_DEAD = "dead"
REASON_UNKNOWN = "unknown_action"


@dataclass
class TickContext:
    """Collects at most one intent per entity for a single tick.

    `world` is optional: without it the context cannot tell whether an entity
    is dead, so the `dead` rejection is skipped. The tick loop always supplies
    it; bare contexts are only used in tests and timing helpers.
    """

    tick_id: int
    start_time_ms: int
    deadline_ms: int
    world: World | None = None

    # entity_id -> the single intent that entity submitted this tick
    intents: dict[str, EntityIntent] = field(default_factory=dict)

    # --- timing -----------------------------------------------------------

    def is_past_deadline(self) -> bool:
        """Check if current time is past the intent deadline."""
        return time.time() * 1000 > self.deadline_ms

    # --- submission -------------------------------------------------------

    def submit_intent(
        self,
        entity_id: str,
        intent: EntityIntent,
        enforce_deadline: bool = True,
    ) -> tuple[bool, str]:
        """Record one intent for `entity_id`.

        Returns (accepted, reason); reason is "" when accepted and otherwise
        one of `late_tick`, `duplicate`, `dead`, `unknown_action`.

        Raises:
            ValueError: If the intent's entity_id does not match `entity_id`.
        """
        if intent.entity_id != entity_id:
            raise ValueError(
                f"Intent for {intent.entity_id!r} submitted as {entity_id!r}"
            )
        if type(intent) not in INTENT_ACTION_TYPES:
            logger.warning(
                "intent_rejected_unknown",
                entity_id=entity_id,
                kind=type(intent).__name__,
            )
            return False, REASON_UNKNOWN
        if enforce_deadline and self.is_past_deadline():
            logger.debug(
                "intent_rejected_late", entity_id=entity_id, tick_id=self.tick_id
            )
            return False, REASON_LATE
        if entity_id in self.intents:
            logger.debug(
                "intent_rejected_duplicate", entity_id=entity_id, tick_id=self.tick_id
            )
            return False, REASON_DUPLICATE
        if self.world is not None:
            entity = self.world.all_entities().get(entity_id)
            if entity is not None and not entity.alive:
                logger.debug(
                    "intent_rejected_dead", entity_id=entity_id, tick_id=self.tick_id
                )
                return False, REASON_DEAD

        self.intents[entity_id] = intent
        return True, REASON_ACCEPTED

    def submit_move_intent(
        self, entity_id: str, direction: Direction, enforce_deadline: bool = True
    ) -> bool:
        """Submit a move intent. Returns True if accepted."""
        accepted, _ = self.submit_intent(
            entity_id,
            MoveIntent(entity_id=entity_id, direction=direction),
            enforce_deadline,
        )
        return accepted

    def submit_collect_intent(
        self, intent: CollectIntent, enforce_deadline: bool = True
    ) -> bool:
        """Submit a collect intent. Returns True if accepted."""
        return self.submit_intent(intent.entity_id, intent, enforce_deadline)[0]

    def submit_eat_intent(
        self, intent: EatIntent, enforce_deadline: bool = True
    ) -> bool:
        """Submit an eat intent. Returns True if accepted."""
        return self.submit_intent(intent.entity_id, intent, enforce_deadline)[0]

    def submit_attack_intent(
        self, intent: AttackIntent, enforce_deadline: bool = True
    ) -> bool:
        """Submit an attack intent. Returns True if accepted."""
        return self.submit_intent(intent.entity_id, intent, enforce_deadline)[0]

    def submit_extract_intent(
        self, intent: ExtractIntent, enforce_deadline: bool = True
    ) -> bool:
        """Submit an extract intent. Returns True if accepted."""
        return self.submit_intent(intent.entity_id, intent, enforce_deadline)[0]

    def submit_pickup_intent(
        self, intent: PickupIntent, enforce_deadline: bool = True
    ) -> bool:
        """Submit a pickup intent. Returns True if accepted."""
        return self.submit_intent(intent.entity_id, intent, enforce_deadline)[0]

    def submit_withdraw_intent(
        self, intent: WithdrawIntent, enforce_deadline: bool = True
    ) -> bool:
        """Submit a withdraw intent. Returns True if accepted."""
        return self.submit_intent(intent.entity_id, intent, enforce_deadline)[0]

    def submit_drop_intent(
        self, intent: DropIntent, enforce_deadline: bool = True
    ) -> bool:
        """Submit a drop intent. Returns True if accepted."""
        return self.submit_intent(intent.entity_id, intent, enforce_deadline)[0]

    def submit_deposit_intent(
        self, intent: DepositIntent, enforce_deadline: bool = True
    ) -> bool:
        """Submit a deposit intent. Returns True if accepted."""
        return self.submit_intent(intent.entity_id, intent, enforce_deadline)[0]

    def submit_craft_intent(
        self, intent: CraftIntent, enforce_deadline: bool = True
    ) -> bool:
        """Submit a craft intent. Returns True if accepted."""
        return self.submit_intent(intent.entity_id, intent, enforce_deadline)[0]

    def submit_equip_intent(
        self, intent: EquipIntent, enforce_deadline: bool = True
    ) -> bool:
        """Submit an equip intent. Returns True if accepted."""
        return self.submit_intent(intent.entity_id, intent, enforce_deadline)[0]

    def submit_place_intent(
        self, intent: PlaceIntent, enforce_deadline: bool = True
    ) -> bool:
        """Submit a place intent. Returns True if accepted."""
        return self.submit_intent(intent.entity_id, intent, enforce_deadline)[0]

    def submit_write_note_intent(
        self, intent: WriteNoteIntent, enforce_deadline: bool = True
    ) -> bool:
        """Submit a write_note intent. Returns True if accepted."""
        return self.submit_intent(intent.entity_id, intent, enforce_deadline)[0]

    def submit_rest_intent(
        self, intent: RestIntent, enforce_deadline: bool = True
    ) -> bool:
        """Submit a rest intent. Returns True if accepted."""
        return self.submit_intent(intent.entity_id, intent, enforce_deadline)[0]

    def submit_say_intent(
        self, intent: SayIntent, enforce_deadline: bool = True
    ) -> bool:
        """Submit a say intent. Returns True if accepted."""
        return self.submit_intent(intent.entity_id, intent, enforce_deadline)[0]

    def submit_wait_intent(
        self, intent: WaitIntent, enforce_deadline: bool = True
    ) -> bool:
        """Submit a wait intent. Returns True if accepted."""
        return self.submit_intent(intent.entity_id, intent, enforce_deadline)[0]

    # --- views ------------------------------------------------------------

    def intents_of(self, intent_type: type[T]) -> dict[str, T]:
        """All intents of one concrete type, keyed by entity_id."""
        return {
            entity_id: intent
            for entity_id, intent in self.intents.items()
            if type(intent) is intent_type
        }

    @property
    def move_intents(self) -> dict[str, Direction]:
        """Movement directions keyed by entity_id (movement phase input)."""
        return {
            entity_id: intent.direction
            for entity_id, intent in self.intents_of(MoveIntent).items()
        }

    @property
    def collect_intents(self) -> dict[str, CollectIntent]:
        """Collect intents keyed by entity_id."""
        return self.intents_of(CollectIntent)

    @property
    def eat_intents(self) -> dict[str, EatIntent]:
        """Eat intents keyed by entity_id."""
        return self.intents_of(EatIntent)
