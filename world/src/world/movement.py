"""Movement validation and conflict resolution."""

from dataclasses import dataclass

import structlog

from .state import World
from .types import DIAGONAL_COMPONENTS, DIRECTION_DELTAS, Direction, Position

logger = structlog.get_logger()


@dataclass(frozen=True)
class MoveClaim:
    """A validated move claim ready for conflict resolution."""

    entity_id: str
    from_pos: Position
    to_pos: Position
    direction: Direction


@dataclass
class MoveResult:
    """Result of move resolution for a single entity."""

    entity_id: str
    success: bool
    from_pos: Position
    to_pos: Position  # Same as from_pos if failed
    failure_reason: str | None = None


class MovementResolver:
    """
    Resolves movement conflicts using claim-resolve-enact pattern.

    Conflict resolution rules (v1):
    - Same destination: winner by lexicographic entity_id, losers stay
    - Swaps (A->B and B->A): both fail
    - Cycles (A->B->C->A): all fail
    - Destination occupied by non-moving entity: fail
    """

    def __init__(self, world: World):
        self.world = world

    def validate_move(self, entity_id: str, direction: Direction) -> MoveClaim | None:
        """
        Validate a move intent and return a MoveClaim if valid.
        Returns None if move is invalid (entity stays in place).
        """
        entity = self.world.get_entity(entity_id)
        from_pos = entity.position
        to_pos = from_pos.offset(direction)

        # Check bounds
        if not self.world.in_bounds(to_pos):
            logger.debug("move_rejected_oob", entity_id=entity_id, to_pos=str(to_pos))
            return None

        # Check walkability
        if not self.world.is_walkable(to_pos):
            logger.debug(
                "move_rejected_not_walkable", entity_id=entity_id, to_pos=str(to_pos)
            )
            return None

        # Check diagonal blocking rule
        if direction in DIAGONAL_COMPONENTS:
            d1, d2 = DIAGONAL_COMPONENTS[direction]
            adj1 = from_pos.offset(d1)
            adj2 = from_pos.offset(d2)
            if not (self.world.is_walkable(adj1) and self.world.is_walkable(adj2)):
                logger.debug(
                    "move_rejected_diagonal_blocked",
                    entity_id=entity_id,
                    direction=direction.name,
                )
                return None

        return MoveClaim(
            entity_id=entity_id,
            from_pos=from_pos,
            to_pos=to_pos,
            direction=direction,
        )

    def resolve_conflicts(self, claims: list[MoveClaim]) -> list[MoveResult]:
        """
        Resolve movement conflicts and return results.

        Algorithm:
        1. Build destination -> claimants mapping
        2. Detect swaps and cycles
        3. Handle same-destination conflicts by priority
        4. Check for occupied destinations
        5. Return results
        """
        if not claims:
            return []

        # Maps for conflict detection
        dest_to_claims: dict[Position, list[MoveClaim]] = {}
        entity_to_claim: dict[str, MoveClaim] = {}

        for claim in claims:
            dest_to_claims.setdefault(claim.to_pos, []).append(claim)
            entity_to_claim[claim.entity_id] = claim

        # Track failed entities
        failed: dict[str, str] = {}  # entity_id -> reason

        # Phase 1: Detect and fail swaps
        for claim in claims:
            if claim.entity_id in failed:
                continue
            # Check if someone is moving from our destination to our position
            for other_claim in dest_to_claims.get(claim.from_pos, []):
                if other_claim.from_pos == claim.to_pos:
                    # Swap detected
                    failed[claim.entity_id] = "swap_conflict"
                    failed[other_claim.entity_id] = "swap_conflict"
                    logger.debug(
                        "swap_detected", e1=claim.entity_id, e2=other_claim.entity_id
                    )

        # Phase 2: Detect and fail cycles (length > 2)
        failed.update(self._detect_cycles(claims, entity_to_claim, failed))

        # Phase 3: Resolve same-destination conflicts
        winners: dict[Position, str] = {}  # destination -> winning entity_id

        for dest, dest_claims in dest_to_claims.items():
            valid_claims = [c for c in dest_claims if c.entity_id not in failed]
            if not valid_claims:
                continue

            if len(valid_claims) == 1:
                winners[dest] = valid_claims[0].entity_id
            else:
                # Multiple claimants: winner by lexicographic entity_id
                valid_claims.sort(key=lambda c: c.entity_id)
                winner = valid_claims[0]
                winners[dest] = winner.entity_id
                for loser in valid_claims[1:]:
                    failed[loser.entity_id] = "same_destination_conflict"
                logger.debug(
                    "same_dest_conflict",
                    dest=str(dest),
                    winner=winner.entity_id,
                    losers=[c.entity_id for c in valid_claims[1:]],
                )

        # Phase 4: Check for occupied destinations (by non-moving entities)
        for dest, winner_id in list(winners.items()):
            occupant = self.world.get_entity_at(dest)
            if occupant and occupant.entity_id not in entity_to_claim:
                # Destination occupied by non-moving entity
                failed[winner_id] = "destination_occupied"
                del winners[dest]
                logger.debug(
                    "dest_occupied", entity_id=winner_id, occupant=occupant.entity_id
                )

        # Build results
        results: list[MoveResult] = []
        for claim in claims:
            if claim.entity_id in failed:
                results.append(
                    MoveResult(
                        entity_id=claim.entity_id,
                        success=False,
                        from_pos=claim.from_pos,
                        to_pos=claim.from_pos,  # Stay in place
                        failure_reason=failed[claim.entity_id],
                    )
                )
            else:
                results.append(
                    MoveResult(
                        entity_id=claim.entity_id,
                        success=True,
                        from_pos=claim.from_pos,
                        to_pos=claim.to_pos,
                    )
                )

        return results

    def _detect_cycles(
        self,
        claims: list[MoveClaim],
        entity_to_claim: dict[str, MoveClaim],
        already_failed: dict[str, str],
    ) -> dict[str, str]:
        """Detect cycles of length > 2 and return failed entities."""
        # Build position-to-entity mapping for cycle detection
        pos_to_entity: dict[Position, str] = {}
        for claim in claims:
            if claim.entity_id not in already_failed:
                pos_to_entity[claim.from_pos] = claim.entity_id

        failed: dict[str, str] = {}
        visited_global: set[str] = set()

        for claim in claims:
            if claim.entity_id in already_failed or claim.entity_id in visited_global:
                continue

            # Follow the chain of moves
            chain: list[str] = []
            visited_chain: set[str] = set()
            current_id: str | None = claim.entity_id

            while current_id and current_id not in visited_chain:
                if current_id in already_failed:
                    break
                visited_chain.add(current_id)
                chain.append(current_id)

                current_claim = entity_to_claim.get(current_id)
                if not current_claim:
                    break

                # Who is at our destination and also moving?
                next_entity = pos_to_entity.get(current_claim.to_pos)
                if (
                    next_entity
                    and next_entity in entity_to_claim
                    and next_entity not in already_failed
                ):
                    current_id = next_entity
                else:
                    current_id = None

            # Check if we found a cycle
            if current_id and current_id in visited_chain:
                # Found cycle - find where it starts
                cycle_start = chain.index(current_id)
                cycle_members = chain[cycle_start:]
                if len(cycle_members) > 2:
                    for member in cycle_members:
                        failed[member] = "cycle_conflict"
                    logger.debug("cycle_detected", members=cycle_members)

            visited_global.update(visited_chain)

        return failed

    def enact_moves(self, results: list[MoveResult]) -> None:
        """Apply successful moves to world state."""
        # Collect successful moves
        moves = [(r.entity_id, r.to_pos) for r in results if r.success]

        # Apply all simultaneously by doing removes then adds
        # (Position index handles this correctly via update_entity_position)
        for entity_id, new_pos in moves:
            self.world.update_entity_position(entity_id, new_pos)


def process_movement_phase(
    world: World, intents: dict[str, Direction]
) -> list[MoveResult]:
    """
    Process movement for a tick.

    Args:
        world: The world state
        intents: Mapping of entity_id to move direction

    Returns:
        List of MoveResults for all entities that submitted move intents
    """
    resolver = MovementResolver(world)

    # Phase A: Validate and create claims
    claims: list[MoveClaim] = []
    for entity_id, direction in intents.items():
        claim = resolver.validate_move(entity_id, direction)
        if claim:
            claims.append(claim)

    # Phase B: Resolve conflicts
    results = resolver.resolve_conflicts(claims)

    # Phase C: Enact moves
    resolver.enact_moves(results)

    return results
