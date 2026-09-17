"""Server-side wolf simulation.

Wolves are ordinary entities (`entity_type == "wolf"`) with no agent attached.
Each tick the simulator despawns strays, spawns newcomers, and submits one
intent per wolf through the same `TickContext` the gRPC agents use.
"""

import random
from typing import Mapping

import structlog

from .events import EntityDespawnedEvent, EntitySpawnedEvent, TickEvents
from .settlement import is_free_walkable
from .state import WOLF_ENTITY_TYPE, Entity, World
from .tick_context import TickContext
from .types import (
    DIRECTION_DELTAS,
    AttackIntent,
    Direction,
    MoveIntent,
    Position,
    chebyshev_distance,
)

logger = structlog.get_logger()

WOLF_TYPE = WOLF_ENTITY_TYPE
# Tuned so a lone settler wins a wolf fight only at the cost of about half
# their health, while two or three attacking together barely get scratched:
# damage is simultaneous, so every extra attacker shortens the fight.
WOLF_MAX_HEALTH = 16

SPAWN_INTERVAL_TICKS = 40
MAX_WOLVES = 3
SPAWN_MIN_DISTANCE = 20
SPAWN_MAX_DISTANCE = 40
CHASE_RADIUS = 8
DESPAWN_DISTANCE = 50
WANDER_CHANCE = 0.5
# When wandering, a wolf that can smell a player (beyond CHASE_RADIUS) drifts
# toward them this often; otherwise wolves spawn 20+ tiles out and never
# meet anyone.
PROWL_CHANCE = 0.6
SPAWN_ATTEMPTS = 400

# (dx, dy) -> Direction, derived from the canonical direction table.
_DELTA_TO_DIRECTION: Mapping[tuple[int, int], Direction] = {
    delta: direction for direction, delta in DIRECTION_DELTAS.items()
}


def is_wolf(entity: Entity) -> bool:
    """True for wolf entities."""
    return entity.entity_type == WOLF_TYPE


class WolfSimulator:
    """Spawns, despawns and steers wolves with a deterministic seeded RNG."""

    def __init__(self, seed: int = 1337):
        self.rng = random.Random(seed)
        self._id_counter = 0

    # --- public API -------------------------------------------------------

    def step(self, world: World, ctx: TickContext, events: TickEvents) -> None:
        """Run one wolf tick: despawn, spawn, then submit wolf intents."""
        players = self._living_players(world)
        self._despawn_strays(world, players, events)
        self._maybe_spawn(world, players, events)
        self._submit_intents(world, ctx)

    # --- internals --------------------------------------------------------

    @staticmethod
    def _living_players(world: World) -> list[Entity]:
        return sorted(
            (e for e in world.living_entities() if not is_wolf(e)),
            key=lambda e: e.entity_id,
        )

    @staticmethod
    def _living_wolves(world: World) -> list[Entity]:
        return sorted(
            (e for e in world.living_entities() if is_wolf(e)),
            key=lambda e: e.entity_id,
        )

    @staticmethod
    def _nearest_player(
        players: list[Entity], position: Position
    ) -> tuple[Entity, int] | None:
        if not players:
            return None
        nearest = min(
            players,
            key=lambda p: (chebyshev_distance(p.position, position), p.entity_id),
        )
        return nearest, chebyshev_distance(nearest.position, position)

    def _despawn_strays(
        self, world: World, players: list[Entity], events: TickEvents
    ) -> None:
        if not players:
            return
        for wolf in self._living_wolves(world):
            nearest = self._nearest_player(players, wolf.position)
            if nearest is None or nearest[1] <= DESPAWN_DISTANCE:
                continue
            position = wolf.position
            world.detach_entity(wolf.entity_id)
            world.discard_entity(wolf.entity_id)
            events.entities_despawned.append(
                EntityDespawnedEvent(
                    entity_id=wolf.entity_id, position=position, reason="too_far"
                )
            )
            logger.info("wolf_despawned", entity_id=wolf.entity_id)

    def _next_wolf_id(self, world: World) -> str:
        while True:
            self._id_counter += 1
            candidate = f"{WOLF_TYPE}_{self._id_counter}"
            if candidate not in world.all_entities():
                return candidate

    def _maybe_spawn(
        self, world: World, players: list[Entity], events: TickEvents
    ) -> None:
        if world.tick == 0 or world.tick % SPAWN_INTERVAL_TICKS != 0:
            return
        if not players:
            return
        if len(self._living_wolves(world)) >= MAX_WOLVES:
            return

        position = self._find_spawn_position(world, players)
        if position is None:
            logger.warning("wolf_spawn_failed", tick=world.tick)
            return

        wolf = Entity(
            entity_id=self._next_wolf_id(world),
            position=position,
            entity_type=WOLF_TYPE,
            health=WOLF_MAX_HEALTH,
            max_health=WOLF_MAX_HEALTH,
            hunger=100,
            max_hunger=100,
        )
        world.add_entity(wolf)
        events.entities_spawned.append(
            EntitySpawnedEvent(
                entity_id=wolf.entity_id,
                position=position,
                entity_type=WOLF_TYPE,
            )
        )
        logger.info("wolf_spawned", entity_id=wolf.entity_id, position=str(position))

    def _find_spawn_position(
        self, world: World, players: list[Entity]
    ) -> Position | None:
        for _ in range(SPAWN_ATTEMPTS):
            anchor = self.rng.choice(players).position
            radius = self.rng.randint(SPAWN_MIN_DISTANCE, SPAWN_MAX_DISTANCE)
            dx = self.rng.randint(-radius, radius)
            dy = self.rng.randint(-radius, radius)
            # Force the sample onto the ring of the chosen radius.
            if self.rng.random() < 0.5:
                dx = radius if self.rng.random() < 0.5 else -radius
            else:
                dy = radius if self.rng.random() < 0.5 else -radius

            candidate = Position(x=anchor.x + dx, y=anchor.y + dy)
            if not is_free_walkable(world, candidate, WOLF_TYPE):
                continue

            nearest = self._nearest_player(players, candidate)
            if nearest is None:
                continue
            distance = nearest[1]
            if SPAWN_MIN_DISTANCE <= distance <= SPAWN_MAX_DISTANCE:
                return candidate
        return None

    def _submit_intents(self, world: World, ctx: TickContext) -> None:
        players = self._living_players(world)
        for wolf in self._living_wolves(world):
            nearest = self._nearest_player(players, wolf.position)

            if nearest is not None and nearest[1] <= 1:
                ctx.submit_intent(
                    wolf.entity_id,
                    AttackIntent(
                        entity_id=wolf.entity_id,
                        target_entity_id=nearest[0].entity_id,
                    ),
                    enforce_deadline=False,
                )
                continue

            if nearest is not None and nearest[1] <= CHASE_RADIUS:
                direction = self._chase_direction(
                    world, wolf.position, nearest[0].position
                )
            elif self.rng.random() < WANDER_CHANCE:
                if nearest is not None and self.rng.random() < PROWL_CHANCE:
                    direction = self._chase_direction(
                        world, wolf.position, nearest[0].position
                    )
                else:
                    direction = self._wander_direction(world, wolf.position)
            else:
                direction = None

            if direction is None:
                continue
            ctx.submit_intent(
                wolf.entity_id,
                MoveIntent(entity_id=wolf.entity_id, direction=direction),
                enforce_deadline=False,
            )

    def _walkable_neighbours(self, world: World, position: Position) -> list[Direction]:
        return [
            direction
            for direction in Direction
            if is_free_walkable(world, position.offset(direction), WOLF_TYPE)
        ]

    def _chase_direction(
        self, world: World, position: Position, target: Position
    ) -> Direction | None:
        step = (
            _sign(target.x - position.x),
            _sign(target.y - position.y),
        )
        greedy = _DELTA_TO_DIRECTION.get(step)
        options = self._walkable_neighbours(world, position)
        if greedy is not None and greedy in options:
            return greedy
        if not options:
            return None
        current = chebyshev_distance(position, target)
        closer = [
            direction
            for direction in options
            if chebyshev_distance(position.offset(direction), target) < current
        ]
        pool = closer or options
        return pool[self.rng.randrange(len(pool))]

    def _wander_direction(self, world: World, position: Position) -> Direction | None:
        options = self._walkable_neighbours(world, position)
        if not options:
            return None
        return options[self.rng.randrange(len(options))]


def _sign(value: int) -> int:
    """-1, 0 or 1."""
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0
